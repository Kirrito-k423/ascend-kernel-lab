#pragma once
#include "kernel_operator.h"

namespace akl::latency {
// 在业务 TPipe 已销毁且外层屏障结束后调用；写回成本不进入起止时间差。
__aicore__ inline void WriteKernelLatency(GM_ADDR output, uint64_t start, uint64_t end) {
    if (!output) return;
    AscendC::TPipe pipe;
    AscendC::TBuf<AscendC::TPosition::VECCALC> buffer;
    pipe.InitBuffer(buffer, 32);
    auto row = buffer.Get<uint64_t>();
    row.SetValue(0, start);
    row.SetValue(1, end);
    row.SetValue(2, AscendC::GetBlockIdx());
    row.SetValue(3, 1);
    AscendC::GlobalTensor<uint64_t> destination;
    destination.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(output));
    AscendC::SetFlag<AscendC::HardEvent::S_MTE3>(EVENT_ID0);
    AscendC::WaitFlag<AscendC::HardEvent::S_MTE3>(EVENT_ID0);
    AscendC::DataCopy(destination[AscendC::GetBlockIdx() * 4], row, 4);
    AscendC::SetFlag<AscendC::HardEvent::MTE3_S>(EVENT_ID0);
    AscendC::WaitFlag<AscendC::HardEvent::MTE3_S>(EVENT_ID0);
}
}  // namespace akl::latency
