# 数据契约草案 v0.1

这是完整能力的目标契约。当前实现使用更小的 `akl.micro.v1` manifest与设备ABI 1，已提供原始记录解码与校验，见 `python/akl/trace.py` 和 [实际运行格式](quickstart.md)。嵌套span及通用事件表尚未实现。

## 运行目录

每次运行独立保存于 `results/<run_id>/`：

- manifest.json：版本、run_id、UTC起始、状态、参数、各仓提交、未提交补丁摘要、编译命令及选项、文件清单。
- 环境：芯片、设备/rank、驱动、固件、CANN、编译器、时钟/功耗设置、设备占用、拓扑和缓存策略。未知为 null 并解释。
- events.jsonl 或带版本头二进制：原始事件。
- samples.csv：每次样本、正确性和状态。
- summary.json、report.md、图片：均可重新生成。

中断保留为 incomplete；重复运行不覆盖旧目录，不将未完成样本纳入有效统计。

## 时钟描述

每个 clock_domain_id 记录后端、频率及依据、分辨率、位宽、对齐状态（unverified/within_device/calibrated）、参与通道和校准产物。频率未知仅显示 tick。

跨域映射额外存 scale、offset、残差、不确定度、校准样本和有效窗口。Host UTC、Host monotonic、设备 tick 分属不同域。

## 事件

| 字段 | 约束 |
| --- | --- |
| run_id / launch_id / stream_id | 隔离运行、调用和流 |
| rank / device_id | 设备与地址归属 |
| core_type / block_id / subblock_id | 逻辑通道，物理 ID 如可得则另存 |
| clock_domain_id / tick | JSON tick 为十进制字符串，二进制 uint64 |
| sequence / event_id / span_id | 区分循环、重复和嵌套 |
| kind | instant / begin / end |
| boundary | issue / completion / observation |
| valid / invalid_reason | 未提交、缺失、回绕不明等原因可追踪 |

每通道头部有 capacity、written、dropped、提交状态。满时不得越界，每 launch 独立复位；缺失端点不构造完整区间，保留孤立点和告警。

## 基准样本

case_id、parameter_set_id、iteration、warmup、seed、rank、clock_domain、begin/end tick、repeat_count、measurement_kind、payload_bytes、wall_time（如有）、correctness、status、error、日志路径。

参数包括 API 精确重载、dtype、shape、方向、对齐、字节 gap/pitch、原始 API 字段、核/卡数、缓存与依赖策略。指标按测量类型计算，failed/skipped 必须有原因。
