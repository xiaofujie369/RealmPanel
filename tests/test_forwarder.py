"""Pure unit tests of the nftables engine; kernel behaviour is covered by live tests."""
import subprocess
import uuid
import pytest
from backend.forwarder import ApplyError, Endpoint, Engine, ForwardFilter, TABLE, base_script, local_listeners, occupied, plan, port_script
from backend.traffic import Meter

U = str(uuid.UUID(int=1))


def rule(**kwargs):
    return {'uuid': U, 'name': 'r1', 'enabled': True, 'listen_host': '0.0.0.0', 'listen_port': 2088, 'remote_host': '198.51.100.7', 'remote_port': 443, 'protocol': 'tcp_udp', 'through': '', **kwargs}


def fake_resolver(host, family):
    return {('example.com', 4): '203.0.113.5', ('example.com', 6): '2001:db8::5'}.get((host, family)) or (host if (':' in host) == (family == 6) and host[0].isdigit() or ':' in host and family == 6 else None)


def test_plan_families_protocols_and_domains():
    endpoints, problems = plan([rule(), rule(uuid=str(uuid.UUID(int=2)), listen_host='::', listen_port=2089, remote_host='example.com', protocol='tcp'), rule(enabled=False, listen_port=2090)], fake_resolver)
    assert not problems
    assert [(e.family, e.proto, e.port, e.target) for e in endpoints] == [(4, 'tcp', 2088, '198.51.100.7'), (4, 'udp', 2088, '198.51.100.7'), (4, 'tcp', 2089, '203.0.113.5'), (6, 'tcp', 2089, '2001:db8::5')]
    _, problems = plan([rule(remote_host='unresolvable.invalid')], lambda h, f: None)
    assert problems and problems[0][0] == U and 'unresolvable.invalid' in problems[0][1]


def test_scripts_are_scoped_and_per_port():
    base = base_script()
    assert all(f'inet {TABLE}' in line for line in base)
    assert sum('vmap @' in line for line in base) == 5  # one O(1) lookup per hook, independent of rule count
    endpoints, _ = plan([rule(through='192.0.2.1'), rule(uuid=str(uuid.UUID(int=2)), listen_host='2001:db8::1', remote_host='2001:db8::2', protocol='udp')], fake_resolver)
    tcp = port_script(('tcp', 2088), tuple(e for e in endpoints if e.proto == 'tcp'))
    text = '\n'.join(base + tcp)
    assert 'flush ruleset' not in text and 'drop' not in text
    assert all(f'inet {TABLE}' in line for line in tcp)
    assert all('_tcp_2088' in line for line in tcp)  # only this port's own chains and map elements
    assert 'meta nfproto ipv4 meta l4proto tcp dnat ip to 198.51.100.7:443' in text
    assert 'snat ip to 192.0.2.1' in text
    assert 'add element inet realmpanel dnat_ports { tcp . 2088 : jump d_tcp_2088 }' in text
    udp = '\n'.join(port_script(('udp', 2088), tuple(e for e in endpoints if e.proto == 'udp')))
    assert udp.index('ip6 daddr 2001:db8::1') < udp.index('meta nfproto ipv4')  # specific address before wildcard
    removed = port_script(('tcp', 2088), None)
    assert removed[0] == 'delete element inet realmpanel dnat_ports { tcp . 2088 }' and 'delete chain inet realmpanel d_tcp_2088' in removed


def test_occupied_detection(tmp_path):
    (tmp_path / 'net').mkdir()
    header = '  sl  local_address rem_address   st\n'
    # 0.0.0.0:22 LISTEN, 127.0.0.1:53 UDP, [::]:8443 LISTEN, ESTABLISHED on 2088 ignored
    (tmp_path / 'net/tcp').write_text(header + '0: 00000000:0016 00000000:0000 0A\n1: 0100007F:0828 0100007F:9999 01\n')
    (tmp_path / 'net/udp').write_text(header + '0: 0100007F:0035 00000000:0000 07\n')
    (tmp_path / 'net/tcp6').write_text(header + '0: 00000000000000000000000000000000:20FB 00000000000000000000000000000000:0000 0A\n')
    (tmp_path / 'net/udp6').write_text(header)
    found = local_listeners(tmp_path)
    assert found == {('tcp', '0.0.0.0', 22), ('udp', '127.0.0.1', 53), ('tcp', '::', 8443)}
    e = lambda proto, port, host=None, family=4: Endpoint(U, 'r', family, proto, host, port, '198.51.100.7', 1)
    assert occupied(e('tcp', 22), found)
    assert occupied(e('tcp', 22, '192.0.2.1'), found)
    assert occupied(e('udp', 53), found) and not occupied(e('udp', 53, '192.0.2.1'), found)
    assert occupied(e('tcp', 8443), found)  # [::] dual-stack listener also owns IPv4
    assert not occupied(e('tcp', 2088), found) and not occupied(e('udp', 22), found)


class FakeKernel:
    def __init__(self, listing=''):
        self.calls, self.listing, self.table, self.scripts = [], listing, False, []

    def __call__(self, *args, script=None, check=True):
        self.calls.append((args, script))
        out, code = '', 0
        if args[:2] == ('nft', 'list'):
            code = 0 if self.table else 1
        elif args[:2] == ('nft', '-f'):
            self.table = True
            self.scripts.append(script)
        elif args[0].startswith('ip') and '-S' in args:
            out = self.listing if 'FORWARD' not in args else '-P FORWARD DROP\n'
        elif args[0].startswith('ip') and '-C' in args:
            code = 1
        return subprocess.CompletedProcess(args, code, out, '')


def engine(tmp_path, kernel):
    (tmp_path / 'net').mkdir(exist_ok=True)
    (tmp_path / 'sys/net/ipv4').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'sys/net/ipv4/ip_forward').write_text('1\n')
    meter = Meter(tmp_path / 'traffic')
    meter.read = lambda: {}
    return Engine(meter, log=lambda m: None, runner=kernel, resolver=fake_resolver, proc=tmp_path)


def test_engine_docker_user_and_idempotent_apply(tmp_path):
    kernel = FakeKernel('-P FORWARD DROP\n-N DOCKER-USER\n-A FORWARD -j DOCKER-USER\n')
    eng = engine(tmp_path, kernel)
    eng.apply([rule()], strict=True)
    restore = [s for a, s in kernel.calls if a[0] == 'iptables-nft-restore'][0]
    assert ':RMP-FORWARD - [0:0]' in restore and '--ctstate DNAT --ctorigdstport 2088' in restore
    assert any(a[:5] == ('iptables-nft', '-w', '5', '-I', 'DOCKER-USER') for a, _ in kernel.calls)
    assert eng.status()['healthy'] and eng.status()['forward_filter']['ipv4'] == 'docker-user'
    applied = sum(a[:2] == ('nft', '-f') for a, _ in kernel.calls)
    eng.apply([rule()])
    assert sum(a[:2] == ('nft', '-f') for a, _ in kernel.calls) == applied  # unchanged ruleset is not reloaded


def test_engine_drops_connections_of_changed_rules_only(tmp_path):
    kernel = FakeKernel()
    eng = engine(tmp_path, kernel)
    other = rule(uuid=str(uuid.UUID(int=2)), listen_port=3000)
    eng.apply([rule(), other], strict=True)
    eng.apply([rule(remote_port=8443), other], strict=True)
    deleted = [a for a, _ in kernel.calls if a[:2] == ('conntrack', '-D')]
    assert {a[a.index('--orig-port-dst') + 1] for a in deleted} == {'2088'}
    assert all('--dst-nat' in a for a in deleted)


def test_engine_strict_rejects_without_ip_forward(tmp_path):
    eng = engine(tmp_path, FakeKernel())
    (tmp_path / 'sys/net/ipv4/ip_forward').write_text('0\n')
    with pytest.raises(ApplyError, match='ip_forward'):
        eng.apply([rule()], strict=True)


def test_forward_filter_not_needed_when_policy_accept():
    calls = []
    def runner(*args, script=None, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '-P FORWARD ACCEPT\n', '')
    flt = ForwardFilter(4, runner)
    flt.sync([])
    assert flt.state == 'not-needed' and not any('restore' in a[0] for a in calls)


def test_editing_one_rule_touches_only_its_ports(tmp_path):
    kernel = FakeKernel()
    eng = engine(tmp_path, kernel)
    rules = [rule(uuid=str(uuid.UUID(int=i)), name=f'r{i}', listen_port=3000 + i) for i in range(1, 41)]
    eng.apply(rules, strict=True)
    assert len(kernel.scripts) == 1
    rules[4] = {**rules[4], 'remote_port': 9443}
    eng.apply(rules, strict=True)
    edit = kernel.scripts[-1]
    assert 'delete table' not in edit and 'flush chain inet realmpanel prerouting' not in edit
    touched = {w for line in edit.splitlines() for w in line.split() if w[:2] in ('d_', 's_', 'c_')}
    assert touched == {'d_tcp_3005', 's_tcp_3005', 'c_tcp_3005', 'd_udp_3005', 's_udp_3005', 'c_udp_3005'}
    deleted = [a for a, _ in kernel.calls if a[:2] == ('conntrack', '-D')]
    assert {a[a.index('--orig-port-dst') + 1] for a in deleted} == {'3005'}
    rules[7] = {**rules[7], 'enabled': False}
    eng.apply(rules, strict=True)
    assert 'delete element inet realmpanel dnat_ports { tcp . 3008 }' in kernel.scripts[-1]
    assert 'add counter' not in kernel.scripts[-1] and 'delete counter' not in kernel.scripts[-1]  # paused rule keeps its counters
    del rules[9]
    eng.apply(rules, strict=True)
    assert f'delete counter inet realmpanel r_{uuid.UUID(int=10).hex}_rx' in kernel.scripts[-1]


def test_problem_in_other_rule_does_not_block_an_edit(tmp_path):
    lookups = {'ok.example': '203.0.113.9'}
    eng = engine(tmp_path, FakeKernel())
    eng.resolve = lambda host, family: (host if host[0].isdigit() else lookups.get(host)) if family == 4 else None
    other = rule(uuid=str(uuid.UUID(int=2)), name='other', listen_port=3002, remote_host='ok.example')
    eng.apply([rule(), other], strict=True)
    del lookups['ok.example']  # DNS of an untouched rule breaks
    eng.apply([rule(remote_port=8443), other], strict=True)  # editing r1 still works
    assert any('other' in p for p in eng.problems)
    with pytest.raises(ApplyError, match='new'):
        eng.apply([rule(remote_port=8443), other, rule(uuid=str(uuid.UUID(int=3)), name='new', listen_port=3003, remote_host='missing.example')], strict=True)
