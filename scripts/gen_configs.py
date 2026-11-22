#!/usr/bin/env python3
"""把 configs/*.json 里的占位符换成这台机器上的真实路径，生成可直接粘贴的配置。

用法（在本目录下）：

    .venv\\Scripts\\python.exe scripts/gen_configs.py                # 只填路径，key 留占位
    .venv\\Scripts\\python.exe scripts/gen_configs.py --user-id 12    # 同时填 userid
    .venv\\Scripts\\python.exe scripts/gen_configs.py --print cursor  # 直接打印某一份

生成物在 configs/generated/（已在 .gitignore 里排除 *.local.json 之外的手写文件，
但生成物本身**不含真实 key**，可以放心提交/分享）。
密钥永远不要把真实值写进这些文件 —— 用环境变量注入，或本机复制后手填。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {
    "<V1_DIR>": V1.as_posix(),
    "<PYTHON_EXE>": str(sys.executable).replace("\\", "/"),
    "<GWL_USER_ID>": "12",
    "<GWL_API_KEY>": "gk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "<GWL_HTTP_AUTH_TOKEN>": "请换成与 GWL_HTTP_AUTH_TOKEN 相同的值（至少 16 位随机串）",
}


def render(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成本机可用的 MCP 客户端接入配置")
    parser.add_argument("--user-id", help="填入 GWL_USER_ID（默认保留占位符 12）")
    parser.add_argument("--python", help="覆盖解释器路径（默认用运行本脚本的 python）")
    parser.add_argument("--out", default=str(V1 / "configs" / "generated"), help="输出目录")
    parser.add_argument("--print", dest="show", help="只打印指定的配置名（不含 .json）")
    parser.add_argument("--check", action="store_true", help="校验所有模板能渲染成合法 JSON 并退出")
    args = parser.parse_args(argv)

    mapping = dict(PLACEHOLDERS)
    if args.python:
        mapping["<PYTHON_EXE>"] = args.python.replace("\\", "/")
    if args.user_id:
        mapping["<GWL_USER_ID>"] = str(args.user_id)

    out_dir = Path(args.out)
    source = V1 / "configs"
    files = sorted(p for p in source.glob("*.json"))
    if not files:
        print("没找到模板：%s" % source, file=sys.stderr)
        return 2

    problems = []
    for path in files:
        text = render(path.read_text(encoding="utf-8"), mapping)
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            problems.append("%s 渲染后不是合法 JSON：%s" % (path.name, exc))
            continue
        if args.show:
            if path.stem == args.show:
                print(text, end="")
                return 0
            continue
        if not args.check:
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / path.name
            target.write_text(text, encoding="utf-8", newline="\n")
            print("写入 %s" % target)

    if args.show and not problems:
        print("没有名为 %s.json 的模板（可用：%s）"
              % (args.show, ", ".join(p.stem for p in files)), file=sys.stderr)
        return 2
    if problems:
        for line in problems:
            print("ERROR " + line, file=sys.stderr)
        return 1
    if args.check:
        print("OK：%d 份模板渲染后都是合法 JSON" % len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
