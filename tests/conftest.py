"""pytest 夹具：假上游 + 用官方 SDK Client 直接连进程内 MCPServer。

不需要真实凭据，也不联网（除了 127.0.0.1 上的假上游）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_upstream import FakeGwl  # noqa: E402

from gwl_mcp.config import Config  # noqa: E402
from gwl_mcp.server import create_server  # noqa: E402


@pytest.fixture(scope="session")
def fake_gwl():
    server = FakeGwl()
    with server:
        yield server


@pytest.fixture
def fake(fake_gwl):
    """fake_gwl 的短别名（HTTP 层测试里读起来更顺）。"""
    return fake_gwl


@pytest.fixture(autouse=True)
def _reset_fake(fake_gwl):
    fake_gwl.reset()
    yield
    fake_gwl.reset()


@pytest.fixture
def config(fake_gwl) -> Config:
    return Config.from_env(dict(fake_gwl.env))


@pytest.fixture
def server(fake_gwl, config):
    return create_server(config)


@pytest.fixture
def client_server(fake_gwl):
    """返回一个 (Client 工厂) 以便测试自定义配置（如 GWL_ENABLE_WRITES=0）。"""

    def build(**env_overrides):
        env = dict(fake_gwl.env)
        env.update({k: str(v) for k, v in env_overrides.items()})
        cfg = Config.from_env(env)
        return create_server(cfg)

    return build


def mcp_client(server):
    """官方 SDK 的进程内客户端（异步上下文管理器）。

    刻意不做成 async fixture：anyio 的 cancel scope 必须在同一个 task 里进出，
    而 pytest-asyncio 的 fixture 拆除跑在另一个 task，会报
    "Attempted to exit cancel scope in a different task"。
    测试里统一写：async with mcp_client(server) as client: ...
    """
    from mcp import Client

    return Client(server)


@pytest.fixture
def api(fake_gwl):
    """裸 ApiClient（用于 HTTP 层单测）。"""
    from gwl_mcp.api import ApiClient

    cfg = Config.from_env(dict(fake_gwl.env))
    fake_gwl.default(200, {"code": 200, "msg": "pong", "data": {"ok": True}})
    return ApiClient(cfg)