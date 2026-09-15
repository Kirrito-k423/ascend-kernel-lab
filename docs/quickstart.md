# 实机运行与真实算子接入

## 已验证环境

Ascend910_9382（A3），48个AIV，CANN 9.1.0-beta.1，npu-smi/驱动26.0.rc1，Python3.10，NumPy1.26.4，Matplotlib3.10.9。初版使用现代 Bisheng `-xasc --npu-arch=dav-2201`；其他版本需单独适配。

执行前查看 `npu-smi info`，选择空闲的逻辑设备。运行器用 ACL 查询 AIV 数量；该环境 UB 属性查询返回0，因此回退到同版本 SoC 平台配置（196608B）并在 manifest 记录来源。

## 编译与运行

在仓库根目录：

```bash
source /usr/local/Ascend/cann/set_env.sh
python3 -m pip install -r requirements.txt
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DAKL_NPU_ARCH=dav-2201
cmake --build build -j2
python3 -m unittest discover -s tests -v

# 三个最小用例
timeout 90 python3 scripts/run_micro.py --device 0 --smoke --samples 2 --warmup 1 --output results/smoke

# 78配置主矩阵：1/8/48 AIV，每配置3次预热+10对采集开关对照
timeout 300 python3 scripts/run_micro.py --device 0 --max-cores 48 --samples 10 --warmup 3 --output results/main

# 28配置补充对照，20对采样
timeout 300 python3 scripts/run_micro.py --device 0 --case-json examples/followup.json --samples 20 --warmup 3 --output results/followup

# 无需NPU，解析原始数据并出图
python3 scripts/report.py results/main
python3 scripts/report.py results/followup
```

依赖已安装时无需重新安装；远程离线环境可直接使用现有 NumPy/Matplotlib。结果目录必须不存在，避免覆盖。超时或错误运行标为 incomplete/failed，不进入有效报告。

## 首批能力

- DataCopy：uint32，GM→UB、UB→GM，32B对齐块、blockCount、gap；每 AIV 使用独立地址片段。跨步写验证间隔未被覆盖。
- GatherMask：uint32，固定模式1..7，自定义全空/全选/交替/随机mask，普通和计数模式，每repeat的1/31/63/64元素，repeat与源/掩码stride。
- 两类API均采用每调用完成同步，报告循环平均耗时；搬入准备、输出校验与日志导出在区间外。
- DataCopy GM→UB 每次等待 MTE2→S；UB→GM 等待 MTE3→S；GatherMask 等待 V→S。它们是串行完成延迟用例，不是异步饱和吞吐用例。
- 固定小工作集重复访问；不声明冷缓存、物理总线带宽或跨卡竞争。
- trace模式校验输出内容和设备返回的保留元素数；plain模式校验输出内容及保护区，设备返回数量仅在trace模式导出。

## 结果文件

每次运行包含 manifest、源码/动态库哈希、每配置输入、每启动输出、trace二进制矩阵、events.jsonl与samples.json。报告生成 samples.csv、summary.csv/json、PNG/SVG、HTML和两种Chrome Trace/Perfetto兼容JSON。

`report.html` 可在本地浏览器打开，按配置切换时间线。原始tick用字符串保存。每配置选取中间一个有效采集样本画图，统计使用全部有效样本；没有挑选最快的时间线。

- `trace_per_core.json`：每核分别归零，只用于核内耗时。
- `trace_raw_unverified.json`：共同原始SYS_CNT起点，显式标为未校准。
- PNG/SVG使用第二种视图，以保留首点差异，不能宣称是经过校准的绝对启动时间。

## 在真实算子中接入记录器

`include/akl/trace/recorder.h` 是独立的header-only设备记录器，不依赖本仓的实验kernel。典型流程：

1. 固定本仓提交，添加 `include/` 到目标编译路径。
2. Host为每次未完成launch独立分配 `AIV数 × akl::kWords × sizeof(uint64_t)` 字节，传入新参数；禁止复用业务输出区。
3. kernel入口创建 `akl::Recorder<true>`，调用 `Mark(event_id)`。关闭版本使用 `Recorder<false>`。
4. 为Flush准备专属、32B对齐的UB暂存区，大小为 `akl::kWords × 8`。初版容量16个事件、每AIV320B，满时记录dropped。
5. 业务完成后调用Flush。初版Flush会使用并等待S→MTE3与MTE3→S的EVENT_ID0，调用方必须确保这两个硬事件ID无未完成业务用途。Flush不隐式等待其他业务流水。
6. Host等待流完成，复制trace buffer再回收。重叠launch/stream使用不同buffer。
7. 固定实验仍用 `python/akl/trace.py`；多级字符串、循环打点及目标接入补丁使用 [语义打点入口](semantic-trace.md)，该新增入口尚未上板。

所有打点本身不插入barrier；调用方定义发射或完成的含义。当前没有实现多卡时间校准、跨进程统一事件协议或任意事件表GUI。

## 继续添加实验

在JSON数组中添加Case字段，参见 `examples/followup.json`；名称必须唯一。参数、序列化和参考结果位于 `python/akl/cases.py`，kernel位于 `kernels/micro.cpp`。每种新API必须同步添加正确性oracle，不能只添加耗时输出。
