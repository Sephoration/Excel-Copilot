"""核心业务逻辑：数据加载 / 探查 / 数据池 / 沙箱 / 可视化 / 会话。

注意：本包不做包级再导出。`core` 与 `agent` 存在互相引用（session → agent.graph，
agent.nodes → core.safe_executor），若在此处急切导入会触发循环导入，请始终按
`from .xxx import Y` 的方式直接导入子模块。
"""
