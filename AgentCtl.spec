# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["agentctl.py"], pathex=["."], binaries=[], datas=[],
    hiddenimports=["agent.transport"], hookspath=[], hooksconfig=[],
    runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-AgentCtl", console=True)
