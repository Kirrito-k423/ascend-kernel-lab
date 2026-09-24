# 把吞吐扫描延伸到平台区

旧10点扫描到32KiB仍在增长，尚不能用于判断最高吞吐。原基准还错误地给所有重载套用了Pad的65535字节上限，并把UB固定限制在128KiB。AKL #33已修正长度单位，以运行时设备容量检查UB；测量内核仍保持原来的每批完成语义，便于复测对照。

扩展已在A3完成[41962次launch的设备验证](../datacopy-a3-capacity/README.md)，小工作集和64MiB环均观察到对应条件下的平台。以下命令要求AKL #33包含`windows/min-loops`选项，独立编译该分支即可运行。A5由用户在内网手动运行。

## 1. 读取实际容量并生成矩阵

先按[A5指南](a5-testing.md)准备同芯片profile和动态库；A3使用其已验证的dav-2201、50MHz配置。不要套用另一机型的UB大小、架构或频率。

```bash
export DEVICE=0
export LIBRARY="$PWD/build-a5/libakl_datacopy.so"  # A3改为其构建输出
export PROFILE="$PWD/a5-profile.json"           # A3改为其profile
export PYTHONPATH="$PWD/python"
npu-smi info
# 确认空闲后查询，Runtime.info也会在驱动不返回UB时读取本CANN的SoC配置。
export UB_BYTES=$(python3 - <<'PY'
import os
from pathlib import Path
from akl.native import Runtime
r=Runtime(Path(os.environ['LIBRARY']), int(os.environ['DEVICE']), symbol='akl_copy', params_size=64)
try: print(r.info()['ub_bytes'])
finally: r.close()
PY
)
# windows=1每批完成；windows=2为有界双UB窗口。分别生成/运行，不能混成一条曲线。
export WINDOWS=2
python3 -m akl.datacopy_sweep plan --ub-bytes "$UB_BYTES" --windows "$WINDOWS" --min-loops 8192 --mode small --output results/plan-small
python3 -m akl.datacopy_sweep plan --ub-bytes "$UB_BYTES" --windows "$WINDOWS" --min-loops 8192 --mode ring --ring-mib 64 --output results/plan-ring
```

扫描固定uint32、DataCopy(params)、单AIV、单向本地GM：

- 每次数据量从32B按倍数增加，并加入14KiB和各batch的实际容量端点。
- batch分别为1/2/4/8/16/32/64，批内使用独立UB槽位，整批完成后才复用。不同batch分别画线。
- small复用小工作集；ring目标64MiB，按整批槽位向上取整。超出槽数或循环上限的小shape显式跳过，不缩小工作集伪装成同条件。
- 每个GM槽至少访问两次；计时包含循环、地址计算、窗口复用等待和最终排空。windows=2让两个独立UB窗口交替搬运，复用前等旧DMA完成；不是全卡HBM峰值。

例如本次A3 UB为196608B：windows=1最大单次196288B，生成162个small配置、90个ring配置；windows=2最大单次98144B，生成146个small配置、74个ring配置。这四组已在A3各测两轮；其他芯片按查询值重生成，不能把A3数量当成A5实测数量。

## 2. 容量端点冒烟，再独立跑两轮

```bash
# small/ring共用相同UB容量约束，先检查每个batch的最大UB用量。
python3 scripts/run_datacopy.py --profile "$PROFILE" --device "$DEVICE" --library "$LIBRARY" \
  --cases results/plan-small/smoke.json --samples 3 --warmup 1 --output results/capacity-smoke || exit 1
for MODE in small ring; do
  for ROUND in 1 2; do
    python3 scripts/run_datacopy.py --profile "$PROFILE" --device "$DEVICE" --library "$LIBRARY" \
      --cases "results/plan-$MODE/cases.json" --samples 20 --warmup 3 \
      --seed "$((20260924 + ROUND))" --output "results/sweep-$MODE-$ROUND" || exit 1
  done
  python3 -m akl.datacopy_sweep report "results/plan-$MODE/plan.json" \
    "results/sweep-$MODE-1" "results/sweep-$MODE-2" --output "results/scan-$MODE" || exit 1
done
```

每个run保存原始tick、正确性输出摘要、环境与占用证据；平台报告再次核对原始数据、完整矩阵、实际UB和同一编译库。每方向每batch单独连线；不把不同batch最快点拼成一条S曲线。

`results/scan-*/throughput-sweep.png`/`.svg`为两轮曲线，`saturation.json`记录每轮带宽、工作集和判定。平台候选要求末尾3个尺寸跨度至少2倍、两轮所有末端带宽差异≤10%、各点p95/p50≤1.10。阈值是预先规定的筛选规则，不是统计显著性结论。

## 3. 什么才算回答了上限问题

`not_observed`代表没有测到平台，不能把最右点叫上限。`candidate_plateau`仅说明该batch/工作集下出现稳定区；还要比较更大的batch、环大小以及计时循环数是否继续增益。固定发射开销、每批排空或单AIV供给不足都可能造成平台，不能直接归因为硬件能力。

若每批完成扫描仍明显增长，用windows=2与windows=1作独立对照；窗口复用前保留完成等待。若要问整卡吞吐，则另外扫描AIV数，并以覆盖全部核完成的共同时间窗口计量。真实算子中的逐次等待应对齐单次完成基线，连续独立搬运应对齐对应吞吐基线。

数据量—带宽常会从低值增长后趋平；横轴和开销模型会改变外观，不能以“画成S形”为验收。这里以重复测量的平台与扩展后收益判断。
