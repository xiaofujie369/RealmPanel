"""Validated portable model shared by API, import and configuration generation."""
import ipaddress
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


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
    interface: str = Field(default='', max_length=15, pattern=r'^[a-zA-Z0-9_.:-]*$')
    tcp_timeout: int | None = Field(default=None, ge=1, le=86400)
    udp_timeout: int | None = Field(default=None, ge=1, le=86400)
    tcp_keepalive: int | None = Field(default=None, ge=0, le=86400)
    remark: str = Field(default='', max_length=2000)

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


class Network(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tcp_timeout: int = Field(default=5, ge=1, le=86400)
    udp_timeout: int = Field(default=30, ge=1, le=86400)
    tcp_keepalive: int = Field(default=15, ge=0, le=86400)


def overlap(a: dict, b: dict) -> bool:
    if a['listen_port'] != b['listen_port']:
        return False
    x, y = a['listen_host'], b['listen_host']
    return x == y or x == '::' or y == '::' or (':' not in x and ':' not in y and '0.0.0.0' in (x, y))


def address(host: str, port: int) -> str:
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def generate(rules: list[dict], settings: dict) -> dict:
    endpoints = []
    for raw in rules:
        r = Rule.model_validate({k: v for k, v in raw.items() if k in Rule.model_fields})
        if not r.enabled:
            continue
        network = {**Network.model_validate(settings).model_dump(), 'no_tcp': r.protocol == 'udp', 'use_udp': r.protocol != 'tcp'}
        for key in Network.model_fields:
            if getattr(r, key) is not None:
                network[key] = getattr(r, key)
        endpoint = {'listen': address(r.listen_host, r.listen_port), 'remote': address(r.remote_host, r.remote_port), 'network': network}
        if r.through:
            endpoint['through'] = r.through
        if r.interface:
            endpoint['interface'] = r.interface
        endpoints.append(endpoint)
    return {'log': {'level': 'info'}, 'network': Network.model_validate(settings).model_dump(), 'endpoints': endpoints}
