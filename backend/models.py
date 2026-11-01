"""Validated portable model shared by API, import and forwarding plan generation."""
import ipaddress
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

# Realm-only options from schema 1. nftables has no connect timeout or keepalive and the
# egress interface follows the routing table, so they are accepted on import and dropped.
LEGACY_KEYS = ('tcp_timeout', 'udp_timeout', 'tcp_keepalive', 'interface')


class Rule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    group: str = Field(default='默认', min_length=1, max_length=100)
    enabled: StrictBool = True
    listen_host: str = '0.0.0.0'
    listen_port: int = Field(ge=1, le=65535)
    remote_host: str
    remote_port: int = Field(ge=1, le=65535)
    protocol: Literal['tcp', 'udp', 'tcp_udp'] = 'tcp_udp'
    through: str = ''
    remark: str = Field(default='', max_length=2000)

    @model_validator(mode='before')
    @classmethod
    def legacy(cls, data):
        if isinstance(data, dict) and any(k in data for k in LEGACY_KEYS):
            data = dict(data)
            if (data.get('interface') or '').strip():
                raise ValueError('nftables 转发由系统路由决定出口网卡，不支持 Interface 参数；请清空后导入')
            for key in LEGACY_KEYS:
                data.pop(key, None)
        return data

    @field_validator('listen_host')
    @classmethod
    def ip(cls, value: str) -> str:
        return str(ipaddress.ip_address(value.strip('[]')))

    @field_validator('through')
    @classmethod
    def through_ip(cls, value: str) -> str:
        return str(ipaddress.ip_address(value.strip('[]'))) if value else ''

    @field_validator('remote_host')
    @classmethod
    def host(cls, value: str) -> str:
        value = value.strip('[]')
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            name = value.encode('idna').decode('ascii')
            if len(name) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p) for p in name.rstrip('.').split('.')):
                raise ValueError('目标地址必须是 IPv4、IPv6 或有效域名')
            return name

    @model_validator(mode='after')
    def families(self):
        listen = ipaddress.ip_address(self.listen_host)
        families = {4, 6} if self.listen_host == '::' else {listen.version}
        try:
            remote = ipaddress.ip_address(self.remote_host)
        except ValueError:
            remote = None
        if remote and remote.version not in families:
            raise ValueError('nftables 不能在 IPv4 与 IPv6 之间转换；目标地址需与监听地址同族（监听 :: 可同时接收两种）')
        if remote and remote.is_loopback and not listen.is_loopback:
            raise ValueError('外部流量不能转发到回环地址；请填写本机网卡 IP，或监听 127.0.0.1')
        if self.through:
            version = ipaddress.ip_address(self.through).version
            if version not in families or (remote and remote.version != version):
                raise ValueError('出口源 IP 必须与监听及目标地址同族')
        return self


def overlap(a: dict, b: dict) -> bool:
    if a['listen_port'] != b['listen_port']:
        return False
    x, y = a['listen_host'], b['listen_host']
    return x == y or x == '::' or y == '::' or (':' not in x and ':' not in y and '0.0.0.0' in (x, y))


def address(host: str, port: int) -> str:
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def generate(rules: list[dict]) -> dict:
    """The forwarding plan handed to the core; disabled rules keep their traffic counters."""
    items = []
    for raw in rules:
        r = Rule.model_validate({k: v for k, v in raw.items() if k in Rule.model_fields})
        items.append({'uuid': raw['uuid'], 'name': r.name, 'enabled': r.enabled, 'listen_host': r.listen_host, 'listen_port': r.listen_port, 'remote_host': r.remote_host, 'remote_port': r.remote_port, 'protocol': r.protocol, 'through': r.through})
    return {'engine': 'nftables', 'rules': items}


def upgrade(body: dict) -> dict:
    """Stored rules from the Realm era: drop options nftables cannot honour."""
    return {k: v for k, v in body.items() if k not in LEGACY_KEYS}
