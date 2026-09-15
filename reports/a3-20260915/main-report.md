# A3 DataCopy / GatherMask 实测

- SoC：Ascend910_9382；可用AIV：48；配置数：78。
- 每配置通过预热、采集开启和关闭对照，全部启动均校验输出；时间频率：50000000 Hz。
- 指标为每次启动中最慢AIV的循环总时间/循环次数，再跨启动取分位数。包含循环和完成同步，不是裸指令延迟。
- 原始SYS_CNT时间线保留首点偏移，但跨核时钟尚未校准；未做跨卡校准。
- Host差值含Python调用、launch、同步及调度噪声，不能直接当作设备插桩开销；不扣除负差值。

| 配置 | AIV | p50 μs/次 | p95 μs/次 |
|---|---:|---:|---:|
| copy_delay_probe | 48 | 0.1659 | 0.1678 |
| copy_gm_ub_p1024_g0_c1 | 1 | 0.1917 | 0.2077 |
| copy_gm_ub_p1024_g0_c48 | 48 | 0.2459 | 0.2515 |
| copy_gm_ub_p1024_g0_c8 | 8 | 0.2166 | 0.2214 |
| copy_gm_ub_p32_g0_c1 | 1 | 0.1056 | 0.1169 |
| copy_gm_ub_p32_g0_c48 | 48 | 0.1428 | 0.1428 |
| copy_gm_ub_p32_g0_c8 | 8 | 0.1352 | 0.1425 |
| copy_gm_ub_p32_g480_c1 | 1 | 0.1367 | 0.1485 |
| copy_gm_ub_p32_g480_c48 | 48 | 0.1630 | 0.1643 |
| copy_gm_ub_p32_g480_c8 | 8 | 0.1466 | 0.1501 |
| copy_gm_ub_p480_g32_c1 | 1 | 0.2378 | 0.2530 |
| copy_gm_ub_p480_g32_c48 | 48 | 0.3994 | 0.4056 |
| copy_gm_ub_p480_g32_c8 | 8 | 0.3270 | 0.3486 |
| copy_gm_ub_p512_g0_c1 | 1 | 0.1464 | 0.1511 |
| copy_gm_ub_p512_g0_c48 | 48 | 0.1805 | 0.1875 |
| copy_gm_ub_p512_g0_c8 | 8 | 0.1747 | 0.1806 |
| copy_gm_ub_p512_g512_c1 | 1 | 0.1447 | 0.1666 |
| copy_gm_ub_p512_g512_c48 | 48 | 0.1748 | 0.1842 |
| copy_gm_ub_p512_g512_c8 | 8 | 0.1620 | 0.1779 |
| copy_ub_gm_p1024_g0_c1 | 1 | 0.2036 | 0.2053 |
| copy_ub_gm_p1024_g0_c48 | 48 | 0.2211 | 0.2311 |
| copy_ub_gm_p1024_g0_c8 | 8 | 0.2020 | 0.2064 |
| copy_ub_gm_p32_g0_c1 | 1 | 0.1313 | 0.1443 |
| copy_ub_gm_p32_g0_c48 | 48 | 0.1653 | 0.1655 |
| copy_ub_gm_p32_g0_c8 | 8 | 0.1500 | 0.1586 |
| copy_ub_gm_p32_g480_c1 | 1 | 0.1545 | 0.1656 |
| copy_ub_gm_p32_g480_c48 | 48 | 0.1789 | 0.1802 |
| copy_ub_gm_p32_g480_c8 | 8 | 0.1709 | 0.1801 |
| copy_ub_gm_p480_g32_c1 | 1 | 0.2522 | 0.2661 |
| copy_ub_gm_p480_g32_c48 | 48 | 0.5291 | 0.5308 |
| copy_ub_gm_p480_g32_c8 | 8 | 0.5147 | 0.5634 |
| copy_ub_gm_p512_g0_c1 | 1 | 0.1578 | 0.1669 |
| copy_ub_gm_p512_g0_c48 | 48 | 0.1941 | 0.1956 |
| copy_ub_gm_p512_g0_c8 | 8 | 0.1836 | 0.1931 |
| copy_ub_gm_p512_g512_c1 | 1 | 0.1622 | 0.1665 |
| copy_ub_gm_p512_g512_c48 | 48 | 0.1939 | 0.1995 |
| copy_ub_gm_p512_g512_c8 | 8 | 0.1791 | 0.1873 |
| empty_c1 | 1 | 0.0106 | 0.0106 |
| empty_c48 | 48 | 0.0106 | 0.0106 |
| empty_c8 | 8 | 0.0106 | 0.0132 |
| gather_custom_all_e1 | 8 | 0.0584 | 0.0624 |
| gather_custom_all_e31 | 8 | 0.0595 | 0.0631 |
| gather_custom_all_e63 | 8 | 0.0612 | 0.0616 |
| gather_custom_all_e64 | 8 | 0.0575 | 0.0577 |
| gather_custom_even_e1 | 8 | 0.0584 | 0.0584 |
| gather_custom_even_e31 | 8 | 0.0556 | 0.0627 |
| gather_custom_even_e63 | 8 | 0.0575 | 0.0575 |
| gather_custom_even_e64 | 8 | 0.0575 | 0.0575 |
| gather_custom_none_e1 | 8 | 0.0550 | 0.0587 |
| gather_custom_none_e31 | 8 | 0.0556 | 0.0559 |
| gather_custom_none_e63 | 8 | 0.0575 | 0.0575 |
| gather_custom_none_e64 | 8 | 0.0575 | 0.0578 |
| gather_custom_random_e1 | 8 | 0.0584 | 0.0624 |
| gather_custom_random_e31 | 8 | 0.0589 | 0.0591 |
| gather_custom_random_e63 | 8 | 0.0575 | 0.0577 |
| gather_custom_random_e64 | 8 | 0.0609 | 0.0609 |
| gather_fixed1_r1 | 8 | 0.0309 | 0.0312 |
| gather_fixed1_r16 | 8 | 0.0531 | 0.0534 |
| gather_fixed1_r4 | 8 | 0.0341 | 0.0344 |
| gather_fixed2_r1 | 8 | 0.0309 | 0.0312 |
| gather_fixed2_r16 | 8 | 0.0531 | 0.0531 |
| gather_fixed2_r4 | 8 | 0.0341 | 0.0375 |
| gather_fixed3_r1 | 8 | 0.0309 | 0.0311 |
| gather_fixed3_r16 | 8 | 0.0516 | 0.0553 |
| gather_fixed3_r4 | 8 | 0.0341 | 0.0344 |
| gather_fixed4_r1 | 8 | 0.0309 | 0.0312 |
| gather_fixed4_r16 | 8 | 0.0516 | 0.0519 |
| gather_fixed4_r4 | 8 | 0.0341 | 0.0344 |
| gather_fixed5_r1 | 8 | 0.0309 | 0.0309 |
| gather_fixed5_r16 | 8 | 0.0516 | 0.0517 |
| gather_fixed5_r4 | 8 | 0.0341 | 0.0344 |
| gather_fixed6_r1 | 8 | 0.0309 | 0.0312 |
| gather_fixed6_r16 | 8 | 0.0516 | 0.0516 |
| gather_fixed6_r4 | 8 | 0.0341 | 0.0344 |
| gather_fixed7_r1 | 8 | 0.0309 | 0.0312 |
| gather_fixed7_r16 | 8 | 0.0475 | 0.0478 |
| gather_fixed7_r4 | 8 | 0.0341 | 0.0342 |
| gather_strided | 1 | 0.0609 | 0.0612 |
