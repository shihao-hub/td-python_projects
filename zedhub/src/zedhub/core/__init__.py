"""core 包：Service 层（数据访问、业务校验、写入编排、错误分类）。

本包只允许被 daemon 进程内的代码引用（http_api/ws/api 以及它们调用的
service/sources/analytics/writes 等）；CLI、MCP 桥、rpc 等薄壳禁止 import
本包（NFR-3：Service 层与壳物理隔离）。
"""
