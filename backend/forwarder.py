"""nftables kernel forwarding engine.

Only `table inet realmpanel` and the iptables chain `RMP-FORWARD` (plus one jump
to it) are ever created, changed or removed. Nothing else in the host firewall is
touched, and there is never a `flush ruleset`.
"""
import ipaddress
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

TABLE = 'realmpanel'
IPT_CHAIN = 'RMP-FORWARD'
MAPS = ('dnat_ports', 'snat_ports', 'count_ports')
# Base chains never change after creation: every packet does one map lookup by
# (protocol, port) and jumps to that port's own small chains. Changing one rule
# therefore only rewrites the chains and map elements of the ports it uses.
BASE = {
    'prerouting': ('type nat hook prerouting priority dstnat; policy accept;', ['fib daddr type local meta l4proto . th dport vmap @dnat_ports']),
    'output': ('type nat hook output priority -100; policy accept;', ['fib daddr type local meta l4proto . th dport vmap @dnat_ports']),
    'postrouting': ('type nat hook postrouting priority srcnat; policy accept;', ['oifname "lo" return', 'ct status dnat meta l4proto . ct original proto-dst vmap @snat_ports']),
    'count_out': ('type filter hook postrouting priority 300; policy accept;', ['ct status dnat meta l4proto . ct original proto-dst vmap @count_ports']),
    'count_in': ('type filter hook input priority 300; policy accept;', ['iifname != "lo" ct status dnat ct direction original meta l4proto . ct original proto-dst vmap @count_ports']),
}
PROC = Path('/proc')


class ApplyError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    """One kernel forward: one address family and one L4 protocol of a rule."""
    rule: str
    name: str
    family: int
    proto: str
    host: str | None  # None = every local address of this family
    port: int
    target: str
    target_port: int
    through: str = ''

    @property
    def key(self) -> tuple:
        return (self.family, self.proto, self.host, self.port)

    @property
    def value(self) -> tuple:
        return (self.target, self.target_port, self.through)

    @property
    def listen(self) -> str:
        host = self.host or ('0.0.0.0' if self.family == 4 else '::')
        return f'[{host}]:{self.port}' if self.family == 6 else f'{host}:{self.port}'

    @property
    def remote(self) -> str:
        return f'[{self.target}]:{self.target_port}' if self.family == 6 else f'{self.target}:{self.target_port}'


def counter_name(rule_uuid: str, direction: str) -> str:
    return f'r_{uuid.UUID(rule_uuid).hex}_{direction}'


def listen_families(host: str) -> tuple[list[int], str | None]:
    if host == '0.0.0.0':
        return [4], None
    if host == '::':
        return [4, 6], None
    return [ipaddress.ip_address(host).version], host


def resolve(host: str, family: int) -> str | None:
    try:
        return str(ipaddress.ip_address(host)) if ipaddress.ip_address(host).version == family else None
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET if family == 4 else socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return None
    addresses = sorted({info[4][0] for info in infos})
    return addresses[0] if addresses else None


def _proc_address(text: str) -> str:
    raw = bytes.fromhex(text)
    if len(raw) == 4:
        return str(ipaddress.IPv4Address(raw[::-1]))
    value = b''.join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
    address = ipaddress.IPv6Address(value)
    return str(address.ipv4_mapped or address)


def local_listeners(proc: Path = PROC) -> set[tuple[str, str, int]]:
    """(proto, address, port) of listening TCP and bound, unconnected UDP sockets."""
    found = set()
    for proto, files, state in (('tcp', ('tcp', 'tcp6'), '0A'), ('udp', ('udp', 'udp6'), '07')):
        for name in files:
            try:
                lines = (proc / 'net' / name).read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 4 or fields[3] != state:
                    continue
                address, port = fields[1].split(':')
                found.add((proto, _proc_address(address), int(port, 16)))
    return found


def occupied(endpoint: Endpoint, listeners: set) -> str | None:
    for proto, address, port in listeners:
        if proto != endpoint.proto or port != endpoint.port:
            continue
        version = ipaddress.ip_address(address).version
        wildcard = address in ('0.0.0.0', '::')
        if address == '::' or (wildcard and version == endpoint.family) or endpoint.host is None and version == endpoint.family or address == endpoint.host:
            return f'{address}:{port}' if version == 4 else f'[{address}]:{port}'
    return None


def sysctl(name: str, proc: Path = PROC) -> str:
    try:
        return (proc / 'sys' / name.replace('.', '/')).read_text().strip()
    except OSError:
        return ''


def plan(rules: list[dict], resolver=resolve) -> tuple[list[Endpoint], list[tuple[str, str]]]:
    """Turn panel rules into kernel endpoints; returns (rule uuid, problem) instead of raising."""
    endpoints, problems = [], []
    for rule in rules:
        if not rule['enabled']:
            continue
        families, host = listen_families(rule['listen_host'])
        if rule.get('through'):
            families = [f for f in families if ipaddress.ip_address(rule['through']).version == f]
        targets = {f: resolver(rule['remote_host'], f) for f in families}
        targets = {f: t for f, t in targets.items() if t}
        if not targets:
            problems.append((rule['uuid'], f'{rule["name"]}：目标 {rule["remote_host"]} 没有可用于 {rule["listen_host"]} 的 {"/".join("IPv" + str(f) for f in families) or "同族"} 地址'))
            continue
        protocols = ('tcp', 'udp') if rule['protocol'] == 'tcp_udp' else (rule['protocol'],)
        for family, target in targets.items():
            for proto in protocols:
                endpoints.append(Endpoint(rule['uuid'], rule['name'], family, proto, host, int(rule['listen_port']), target, int(rule['remote_port']), rule.get('through') or ''))
    return endpoints, problems


def _match(endpoint: Endpoint, ct: bool) -> str:
    ip = 'ip' if endpoint.family == 4 else 'ip6'
    if endpoint.host is None:
        return f'meta nfproto ipv{endpoint.family}'
    return f'ct original {ip} daddr {endpoint.host}' if ct else f'{ip} daddr {endpoint.host}'


def base_script() -> list[str]:
    lines = [f'add table inet {TABLE}']
    lines += [f'add map inet {TABLE} {name} {{ type inet_proto . inet_service : verdict; }}' for name in MAPS]
    for chain, (spec, rules) in BASE.items():
        lines += [f'add chain inet {TABLE} {chain} {{ {spec} }}', f'flush chain inet {TABLE} {chain}']
        lines += [f'add rule inet {TABLE} {chain} {rule}' for rule in rules]
    return lines


def port_chains(key: tuple[str, int]) -> dict[str, str]:
    proto, port = key
    return {'dnat_ports': f'd_{proto}_{port}', 'snat_ports': f's_{proto}_{port}', 'count_ports': f'c_{proto}_{port}'}


def port_script(key: tuple[str, int], endpoints: tuple['Endpoint', ...] | None) -> list[str]:
    """Rewrite, or with no endpoints remove, the chains of one (protocol, port)."""
    proto, port = key
    chains = port_chains(key)
    element = f'{proto} . {port}'
    lines = []
    if not endpoints:
        for name, chain in chains.items():
            lines += [f'delete element inet {TABLE} {name} {{ {element} }}', f'flush chain inet {TABLE} {chain}', f'delete chain inet {TABLE} {chain}']
        return lines
    body = {name: [] for name in chains}
    # Specific listen addresses before wildcards: the first matching DNAT wins.
    for e in sorted(endpoints, key=lambda e: (e.host is None, e.family, e.host or '')):
        ip = 'ip' if e.family == 4 else 'ip6'
        destination = f'{e.target}:{e.target_port}' if e.family == 4 else f'[{e.target}]:{e.target_port}'
        body['dnat_ports'].append(f'{_match(e, False)} meta l4proto {proto} dnat {ip} to {destination}')
        source = f'snat {ip} to {e.through}' if e.through else 'masquerade'
        body['snat_ports'].append(f'{_match(e, True)} {ip} daddr {e.target} {proto} dport {e.target_port} {source}')
        body['count_ports'].append(f'{_match(e, True)} ct direction original counter name {counter_name(e.rule, "rx")}')
        body['count_ports'].append(f'{_match(e, True)} ct direction reply counter name {counter_name(e.rule, "tx")}')
    for name, chain in chains.items():
        lines += [f'add chain inet {TABLE} {chain}', f'flush chain inet {TABLE} {chain}']
        lines += [f'add rule inet {TABLE} {chain} {rule}' for rule in body[name]]
        lines.append(f'add element inet {TABLE} {name} {{ {element} : jump {chain} }}')
    return lines


def run(*args, script: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(list(args), input=script, text=True, capture_output=True, timeout=15)
    except FileNotFoundError:
        result = subprocess.CompletedProcess(list(args), 127, '', f'{args[0]}: 未安装')
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:500] or f'{args[0]} 返回 {result.returncode}')
    return result


class ForwardFilter:
    """Lets our DNAT connections through an iptables FORWARD chain whose policy is DROP (Docker, ufw)."""

    def __init__(self, family: int, runner=run):
        self.family = family
        self.runner = runner
        self.binary = None
        self.state = 'unknown'

    def detect(self) -> str | None:
        names = ('iptables-nft', 'iptables-legacy') if self.family == 4 else ('ip6tables-nft', 'ip6tables-legacy')
        for binary in names:
            result = self.runner(binary, '-w', '5', '-S', 'FORWARD', check=False)
            if result.returncode == 0 and result.stdout.strip():
                return binary
        return None

    def sync(self, endpoints: list[Endpoint]) -> None:
        self.binary = self.binary or self.detect()
        if not self.binary:
            self.state = 'not-needed'
            return
        listing = self.runner(self.binary, '-w', '5', '-S', check=False).stdout.splitlines()
        drop = '-P FORWARD DROP' in listing
        parent = 'DOCKER-USER' if '-N DOCKER-USER' in listing else 'FORWARD'
        ours = f'-N {IPT_CHAIN}' in listing
        if not drop and parent == 'FORWARD' and not ours:
            self.state = 'not-needed'
            return
        rules = [f':{IPT_CHAIN} - [0:0]']
        for e in endpoints:
            if e.family != self.family:
                continue
            destination = f' --ctorigdst {e.host}' if e.host else ''
            rules.append(f'-A {IPT_CHAIN} -p {e.proto} -m conntrack --ctstate DNAT --ctorigdstport {e.port}{destination} -m comment --comment realmpanel -j ACCEPT')
        restore = {'iptables-nft': 'iptables-nft-restore', 'iptables-legacy': 'iptables-legacy-restore', 'ip6tables-nft': 'ip6tables-nft-restore', 'ip6tables-legacy': 'ip6tables-legacy-restore'}[self.binary]
        self.runner(restore, '-w', '5', '--noflush', script='*filter\n' + '\n'.join(rules) + '\nCOMMIT\n')
        jump = ['-j', IPT_CHAIN]
        if self.runner(self.binary, '-w', '5', '-C', parent, *jump, check=False).returncode:
            self.runner(self.binary, '-w', '5', '-I', parent, '1', *jump)
        self.state = parent.lower()

    def remove(self) -> None:
        self.binary = self.binary or self.detect()
        if not self.binary:
            return
        for parent in ('DOCKER-USER', 'FORWARD'):
            while self.runner(self.binary, '-w', '5', '-D', parent, '-j', IPT_CHAIN, check=False).returncode == 0:
                pass
        self.runner(self.binary, '-w', '5', '-F', IPT_CHAIN, check=False)
        self.runner(self.binary, '-w', '5', '-X', IPT_CHAIN, check=False)


class Engine:
    def __init__(self, meter, log=print, runner=run, resolver=resolve, proc: Path = PROC):
        self.meter = meter
        self.log = log
        self.runner = runner
        self.lookup = resolver
        self.resolved: dict[tuple, str] = {}
        self.proc = proc
        self.active: dict[tuple, Endpoint] = {}
        self.ports: dict[tuple, tuple] = {}
        self.counters: set[str] = set()
        self.seen: dict[str, dict] = {}
        self.ready = False
        self.rule_problems: dict[str, list[str]] = {}
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.error: str | None = None
        self.filters = {4: ForwardFilter(4, runner), 6: ForwardFilter(6, runner)}

    def resolve(self, host: str, family: int) -> str | None:
        # Keep forwarding to the last known address while DNS is temporarily unavailable.
        address = self.lookup(host, family)
        if address:
            if self.resolved.get((host, family)) not in (None, address):
                self.log(f'域名 {host} 解析变化：{self.resolved[(host, family)]} → {address}')
            self.resolved[(host, family)] = address
        return address or self.resolved.get((host, family))

    def nft(self, *args, script=None) -> str:
        return self.runner('nft', *args, script=script).stdout

    def table_exists(self) -> bool:
        return self.runner('nft', 'list', 'table', 'inet', TABLE, check=False).returncode == 0

    def apply(self, rules: list[dict], strict: bool = False) -> None:
        """Bring the kernel to `rules`, touching only ports whose endpoints changed.

        strict (a user edit): reject problems of rules that are new or changed in
        this request; problems of untouched rules are reported, never blocking.
        """
        endpoints, problems = plan(rules, self.resolve)
        changed = {r['uuid'] for r in rules if self.seen.get(r['uuid']) != r}
        listeners = local_listeners(self.proc)
        accepted, reported = [], [msg for _, msg in problems]
        per_rule: dict[str, list[str]] = {}
        for uid, msg in problems:
            per_rule.setdefault(uid, []).append(msg)
        rejected = [msg for uid, msg in problems if strict and uid in changed]
        for e in endpoints:
            owner = occupied(e, listeners)
            if not owner:
                accepted.append(e)
                continue
            message = f'{e.name}：{e.proto.upper()} {e.listen} 已被本机进程占用（{owner}），nftables 不会劫持该端口'
            per_rule.setdefault(e.rule, []).append(message)
            (rejected if strict and e.rule in changed else reported).append(message)
        if strict and sysctl('net.ipv4.ip_forward', self.proc) != '1' and any(e.family == 4 and e.rule in changed for e in accepted):
            rejected.append('系统未开启 IPv4 转发（net.ipv4.ip_forward=0）')
        if rejected:
            raise ApplyError('；'.join(dict.fromkeys(rejected)))

        grouped: dict[tuple, list] = {}
        for e in accepted:
            grouped.setdefault((e.proto, e.port), []).append(e)
        desired = {k: tuple(sorted(v, key=lambda e: (e.family, e.host or '', e.rule))) for k, v in grouped.items()}
        counters = {counter_name(r['uuid'], d) for r in rules for d in ('rx', 'tx')}

        lines = []
        if not self.ready or not self.table_exists():
            # Start (or our table vanished): build once, keeping accumulated traffic.
            if self.table_exists():
                self.meter.accumulate(self.meter.read())
                lines.append(f'delete table inet {TABLE}')
            lines += base_script()
            self.ports, self.counters = {}, set()
        created = sorted(counters - self.counters)
        lines += [f'add counter inet {TABLE} {name}' for name in created]
        keys = sorted(k for k in set(desired) | set(self.ports) if desired.get(k) != self.ports.get(k))
        for key in keys:
            lines += port_script(key, desired.get(key))
        obsolete = sorted(self.counters - counters)
        if obsolete:
            self.meter.accumulate({k: v for k, v in self.meter.read().items() if k in obsolete})
        lines += [f'delete counter inet {TABLE} {name}' for name in obsolete]
        if lines:
            self.nft('-f', '-', script='\n'.join(lines) + '\n')
            self.meter.created(created)
            self.ready = True
            if keys:
                names = sorted({e.name for k in keys for e in desired.get(k, ()) + self.ports.get(k, ())})
                ports = '、'.join(f'{p}/{n}' for p, n in keys[:8]) + ('…' if len(keys) > 8 else '')
                more = '…' if len(names) > 5 else ''
                self.log(f'已更新 {len(keys)} 个端口（{ports}），涉及规则：{"、".join(names[:5])}{more}；其余 {len(set(desired) - set(keys))} 个端口未改动')
        for family, flt in self.filters.items():
            try:
                flt.sync(accepted)
            except (OSError, RuntimeError) as exc:
                flt.state = 'error'
                reported.append(f'IPv{family} FORWARD 放行规则同步失败：{exc}')
        new = {e.key: e for e in accepted}
        for e in self.active.values():
            if e.key not in new or new[e.key].value != e.value:
                self.drop_connections(e)
        self.active, self.ports, self.counters = new, desired, counters
        self.seen = {r['uuid']: r for r in rules}
        warnings = []
        if any(e.family == 6 for e in accepted) and sysctl('net.ipv6.conf.all.forwarding', self.proc) != '1':
            warnings.append('IPv6 转发未开启（net.ipv6.conf.all.forwarding=0），仅本机发起的 IPv6 连接会被转发')
        if sysctl('net.ipv4.ip_forward', self.proc) != '1' and any(e.family == 4 for e in accepted):
            reported.append('系统未开启 IPv4 转发（net.ipv4.ip_forward=0）')
        for message in reported:
            if message not in self.problems:
                self.log('警告：' + message)
        self.problems, self.warnings, self.error = reported, warnings, None
        self.rule_problems = per_rule

    def drop_connections(self, e: Endpoint) -> None:
        # Only DNAT entries whose original destination is this endpoint; outgoing
        # connections of the host to the same remote port are not DNAT and are kept.
        args = ['conntrack', '-D', '-f', 'ipv4' if e.family == 4 else 'ipv6', '-p', e.proto, '--orig-port-dst', str(e.port), '--dst-nat']
        if e.host:
            args += ['--orig-dst', e.host]
        self.runner(*args, check=False)

    def teardown(self) -> None:
        try:
            if self.table_exists():
                self.meter.accumulate(self.meter.read())
                self.nft('delete', 'table', 'inet', TABLE)
        finally:
            for e in self.active.values():
                self.drop_connections(e)
            for flt in self.filters.values():
                flt.remove()
            self.active, self.ports, self.counters, self.seen, self.ready = {}, {}, set(), {}, False
            self.log('已移除 nftables 转发表')

    def rule_status(self, rule_uuid: str) -> dict:
        """Kernel state of a single rule only, for its diagnosis."""
        endpoints = sorted((e for e in self.active.values() if e.rule == rule_uuid), key=lambda e: (e.proto, e.family))
        return {
            'running': self.table_exists(), 'known': rule_uuid in self.seen,
            'endpoints': [{'proto': e.proto, 'family': e.family, 'listen': e.listen, 'remote': e.remote, 'target': e.target, 'target_port': e.target_port, 'through': e.through} for e in endpoints],
            'problems': self.rule_problems.get(rule_uuid, []),
            'warnings': self.warnings if any(e.family == 6 for e in endpoints) else [],
            'ip_forward': sysctl('net.ipv4.ip_forward', self.proc) == '1',
            'forward_filter': {f'ipv{k}': v.state for k, v in self.filters.items()},
        }

    def status(self) -> dict:
        running = self.table_exists()
        active = sorted(self.active.values(), key=lambda e: (e.port, e.family, e.proto))
        listeners = sorted({(e.host or ('0.0.0.0' if e.family == 4 else '::'), e.port, e.proto) for e in active})
        try:
            conntrack = {'count': int(sysctl('net.netfilter.nf_conntrack_count', self.proc) or 0), 'max': int(sysctl('net.netfilter.nf_conntrack_max', self.proc) or 0)}
        except ValueError:
            conntrack = {'count': 0, 'max': 0}
        healthy = (running or not active) and not self.problems and not self.error
        return {
            'engine': 'nftables', 'running': running, 'healthy': healthy, 'idle': not active,
            'forwards': len(active), 'listeners': listeners, 'active': [[e.listen, e.proto, e.remote] for e in active],
            'missing': self.problems + ([self.error] if self.error else []), 'warnings': self.warnings,
            'ip_forward': sysctl('net.ipv4.ip_forward', self.proc) == '1', 'ipv6_forward': sysctl('net.ipv6.conf.all.forwarding', self.proc) == '1',
            'forward_filter': {f'ipv{k}': v.state for k, v in self.filters.items()}, 'conntrack': conntrack,
        }
