# 参考 DebugClock 调查

检查日期：2026-09-15。通过 Git 读取 `deepep_ccd`，固定提交为 `19c40e99a622c2778c4c5ce94aa1f6adc33effbc`。以下结论仅针对该版本。

## 结论

**现有输出不能恢复各 AIV 的首点时间差，但仓库已经能生成 PNG。**

| 位置 | 观察 | 影响 |
| --- | --- | --- |
| elastic_dispatch.cpp:28 | DebugClock 记录 GetSystemCycle 并设置有效位 | 内部曾持有原始时间戳 |
| Init，2071 行 | 首点在初始化入口 | 不等于硬件精确启动时刻 |
| Process，2534 行起 | 导出相邻时间戳差到 int32；无效/溢出为 -1 | 原始起点丢失 |
| elastic_dispatch_clock_host.h | 导出 CSV，依赖业务 workspace 布局 | 接入耦合；同一路径发布最新快照 |
| analyze_dispatch_time.py:162 起 | 每条 AIV 柱子从零累计阶段耗时 | 是堆叠阶段图，没有起点偏移 |
| 同脚本，350 行 | --cycle-us 默认 0.001 | 对 50MHz tick 应为 0.02；调用者可能覆盖默认值，不能断言历史图都错 |
| 同脚本，90 行附近 | 负值与大于 500000 的值置零 | 缺失和超阈值变成零耗时 |
| 同脚本，392 行附近 | 输出各 rank PNG | 当前分支已有图片能力 |

来源：[算子源码](https://gitcode.com/ChenDonYY/ascend_deepep/blob/19c40e99a622c2778c4c5ce94aa1f6adc33effbc/kernels/elastic_dispatch.cpp)、[Host 导出](https://gitcode.com/ChenDonYY/ascend_deepep/blob/19c40e99a622c2778c4c5ce94aa1f6adc33effbc/kernels/elastic_dispatch_clock_host.h)、[绘图脚本](https://gitcode.com/ChenDonYY/ascend_deepep/blob/19c40e99a622c2778c4c5ce94aa1f6adc33effbc/test/analyze_dispatch_time.py)。

## 时钟证据边界

官方 [GetSystemCycle，CANN 9.1 beta](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910beta1/API/ascendcopapi/atlasascendc_api_07_0282.html) 说明该接口位于 PIPE_S，按 50MHz 换算，测其他流水需处理同步。该页没有提供跨卡时间戳可直接比较的保证，也不能仅凭“系统”二字就认为各核可对齐。

依次验证同核计时、同设备跨核关系、跨设备映射。若同设备可比较，保留原始起点即可展示偏移；否则先分域显示。跨卡需估计偏移、漂移与误差，或保持每卡独立时间线。

## 其他官方依据

- [PrintTimeStamp](https://www.hiascend.com/document/detail/zh/canncommercial/900/API/ascendcopapi/atlasascendc_api_07_00002.html)：评估作为快速接入后端，需匹配工具链并量化开销。
- [存储与搬运单元](https://www.hiascend.com/document/detail/en/canncommercial/800/opdevg/Ascendcopdevg/atlas_ascendc_10_0010.html)：cache line 随硬件不同，不能统一硬编码512B。
- [GatherMask](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/850/API/ascendcopapi/atlasascendc_api_07_0071.html)：固定与自定义模式作为首批入口；部署时再次核对版本。
- [DataCopyPad](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/80RC3alpha003/apiref/opdevgapi/atlasascendc_api_07_0256.html)：不同方向的长度与间隔单位需分别核对，不能混用重载语义。
