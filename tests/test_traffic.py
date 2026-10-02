import uuid
from backend.traffic import Meter, TABLE


def test_persistence_and_kernel_counter_reset(tmp_path):
    name = 'r_' + uuid.uuid4().hex + '_rx'
    meter = Meter(tmp_path)
    meter.accumulate({name: 1024})
    meter.accumulate({name: 2048})
    meter.db.close()
    meter = Meter(tmp_path)
    meter.accumulate({name: 2048})
    assert meter.db.execute('SELECT total FROM counters').fetchone()[0] == 2048
    meter.accumulate({name: 100})
    assert meter.db.execute('SELECT total FROM counters').fetchone()[0] == 2148


def test_scoped_rules_and_disabled_rule(tmp_path):
    meter = Meter(tmp_path)
    scripts = []
    def nft(*args, script=None):
        if script is not None:
            scripts.append(script)
            return ''
        return '{"nftables":[]}'
    meter.nft = nft
    rule = {'uuid': str(uuid.uuid4()), 'enabled': True, 'listen_host': '::1', 'listen_port': 35000, 'protocol': 'tcp_udp'}
    meter.configure([rule])
    assert not meter.configuration_error
    assert 'ip6 daddr ::1 tcp dport 35000' in scripts[-1]
    assert 'ip6 saddr ::1 udp sport 35000' in scripts[-1]
    assert 'flush ruleset' not in scripts[-1] and 'drop' not in scripts[-1]
    assert all(TABLE in line for line in scripts[-1].splitlines())
    meter.configure([{**rule, 'enabled': False}])
    assert 'add rule' not in scripts[-1]


def test_permission_failure_is_unavailable(tmp_path):
    meter = Meter(tmp_path)
    def denied(*args, **kwargs):
        raise RuntimeError('Operation not permitted')
    meter.nft = denied
    meter.configure([])
    result = meter.snapshot()
    assert not result['available'] and 'not permitted' in result['error']
