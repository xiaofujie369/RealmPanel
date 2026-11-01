import uuid
from backend.traffic import Meter


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


def test_recreated_counter_keeps_total(tmp_path):
    rid = uuid.uuid4()
    name = f'r_{rid.hex}_tx'
    meter = Meter(tmp_path)
    meter.accumulate({name: 5000})
    meter.created([name])  # kernel counter recreated at 0 after upgrade or reboot
    meter.accumulate({name: 10})
    meter.read = lambda: {name: 10}
    snap = meter.snapshot()
    assert snap['available'] and snap['rules'][str(rid)] == {'rx_bytes': 0, 'tx_bytes': 5010, 'total_bytes': 5010}


def test_permission_failure_is_unavailable(tmp_path):
    meter = Meter(tmp_path)
    def denied(*args, **kwargs):
        raise RuntimeError('Operation not permitted')
    meter.nft = denied
    meter.created([])
    result = meter.snapshot()
    assert not result['available'] and 'not permitted' in result['error']
