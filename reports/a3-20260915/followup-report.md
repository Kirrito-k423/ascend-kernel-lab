# A3 DataCopy / GatherMask 实测

- SoC：Ascend910_9382；可用AIV：48；配置数：28。
- 每配置通过预热、采集开启和关闭对照，全部启动均校验输出；时间频率：50000000 Hz。
- 指标为每次启动中最慢AIV的循环总时间/循环次数，再跨启动取分位数。包含循环和完成同步，不是裸指令延迟。
- 原始SYS_CNT时间线保留首点偏移，但跨核时钟尚未校准；未做跨卡校准。
- Host差值含Python调用、launch、同步及调度噪声，不能直接当作设备插桩开销；不扣除负差值。

| 配置 | AIV | p50 μs/次 | p95 μs/次 |
|---|---:|---:|---:|
| copy_control_op1_c1_gap0 | 1 | 0.1462 | 0.1661 |
| copy_control_op1_c1_gap32 | 1 | 0.2313 | 0.2389 |
| copy_control_op1_c48_gap0 | 48 | 0.1731 | 0.1805 |
| copy_control_op1_c48_gap32 | 48 | 0.4402 | 0.4450 |
| copy_control_op4_c1_gap0 | 1 | 0.1556 | 0.1620 |
| copy_control_op4_c1_gap32 | 1 | 0.2592 | 0.2703 |
| copy_control_op4_c48_gap0 | 48 | 0.1886 | 0.1912 |
| copy_control_op4_c48_gap32 | 48 | 0.5386 | 0.5413 |
| copy_shape_op1_b1 | 48 | 0.1444 | 0.1447 |
| copy_shape_op1_b4 | 48 | 0.1489 | 0.1506 |
| copy_shape_op1_b64 | 48 | 0.3470 | 0.3560 |
| copy_shape_op4_b1 | 48 | 0.1680 | 0.1681 |
| copy_shape_op4_b4 | 48 | 0.1691 | 0.1713 |
| copy_shape_op4_b64 | 48 | 0.3641 | 0.3669 |
| gather_48_p1_r1 | 48 | 0.0342 | 0.0360 |
| gather_48_p1_r16 | 48 | 0.0628 | 0.0650 |
| gather_48_p7_r1 | 48 | 0.0352 | 0.0366 |
| gather_48_p7_r16 | 48 | 0.0591 | 0.0597 |
| gather_shared_custom_mask | 8 | 0.0603 | 0.0614 |
| loop_check_op0_n1 | 8 | 0.0600 | 0.0600 |
| loop_check_op0_n256 | 8 | 0.0102 | 0.0102 |
| loop_check_op0_n8 | 8 | 0.0150 | 0.0175 |
| loop_check_op1_n1 | 8 | 0.1900 | 0.2300 |
| loop_check_op1_n256 | 8 | 0.1639 | 0.1794 |
| loop_check_op1_n8 | 8 | 0.1800 | 0.1854 |
| loop_check_op2_n1 | 8 | 0.1400 | 0.1600 |
| loop_check_op2_n256 | 8 | 0.0294 | 0.0294 |
| loop_check_op2_n8 | 8 | 0.0450 | 0.0450 |
