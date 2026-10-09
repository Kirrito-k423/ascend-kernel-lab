#include "kernel_operator.h"
#include "simt_api/asc_simt.h"
using namespace AscendC;

template<int Op>
__simd_callee__ inline void RegOp(Reg::RegTensor<float>& x,
                                 Reg::RegTensor<float>& rhs, Reg::MaskReg& mask) {
    if constexpr (Op == 0) Reg::Add(x, x, rhs, mask);
    else if constexpr (Op == 1) Reg::Sub(x, x, rhs, mask);
    else if constexpr (Op == 2) Reg::Mul(x, x, rhs, mask);
    else Reg::Div(x, x, rhs, mask);
}

template<int Op, int Groups>
__simd_vf__ inline void RegVF(__ubuf__ float* values, __ubuf__ float* rhs,
                             uint32_t n, uint32_t steps, uint32_t lanes) {
    uint32_t offset = 0;
    // 四条互不依赖的向量链；总元素数、每元素计算量与其他实现完全相同。
    if constexpr (Groups == 4) {
        auto all = Reg::CreateMask<float, Reg::MaskPattern::ALL>();
        for (; offset + 4 * lanes <= n; offset += 4 * lanes) {
            Reg::RegTensor<float> x0, x1, x2, x3, y0, y1, y2, y3;
            Reg::LoadAlign(x0, values + offset);
            Reg::LoadAlign(x1, values + offset + lanes);
            Reg::LoadAlign(x2, values + offset + 2 * lanes);
            Reg::LoadAlign(x3, values + offset + 3 * lanes);
            Reg::LoadAlign(y0, rhs + offset);
            Reg::LoadAlign(y1, rhs + offset + lanes);
            Reg::LoadAlign(y2, rhs + offset + 2 * lanes);
            Reg::LoadAlign(y3, rhs + offset + 3 * lanes);
            for (uint32_t j = 0; j < steps; ++j) {
                RegOp<Op>(x0, y0, all); RegOp<Op>(x1, y1, all);
                RegOp<Op>(x2, y2, all); RegOp<Op>(x3, y3, all);
            }
            Reg::StoreAlign(values + offset, x0, all);
            Reg::StoreAlign(values + offset + lanes, x1, all);
            Reg::StoreAlign(values + offset + 2 * lanes, x2, all);
            Reg::StoreAlign(values + offset + 3 * lanes, x3, all);
        }
    }
    uint32_t remaining = n - offset;
    for (; offset < n; offset += lanes) {
        Reg::RegTensor<float> x, y;
        auto mask = Reg::UpdateMask<float>(remaining);
        Reg::LoadAlign(x, values + offset); Reg::LoadAlign(y, rhs + offset);
        for (uint32_t j = 0; j < steps; ++j) RegOp<Op>(x, y, mask);
        Reg::StoreAlign(values + offset, x, mask);
    }
}

template<int Threads, int Op>
__simt_vf__ __aicore__ LAUNCH_BOUND(Threads) inline void SimtVF(
    __ubuf__ float* values, __ubuf__ float* rhs, uint32_t n, uint32_t steps) {
    for (uint32_t i = threadIdx.x; i < n; i += Threads) {
        float x = values[i], y = rhs[i];
        for (uint32_t j = 0; j < steps; ++j) {
            if constexpr (Op == 0) x += y;
            else if constexpr (Op == 1) x -= y;
            else if constexpr (Op == 2) x *= y;
            else x /= y;
        }
        values[i] = x;
    }
}

__aicore__ inline void Complete() {
    SetFlag<HardEvent::V_S>(EVENT_ID0); WaitFlag<HardEvent::V_S>(EVENT_ID0);
}

template<int Op>
__aicore__ inline void TensorWork(LocalTensor<float> x, LocalTensor<float> y,
                                uint32_t n, uint32_t steps) {
    for (uint32_t j = 0; j < steps; ++j) {
        if constexpr (Op == 0) Add(x, x, y, n);
        else if constexpr (Op == 1) Sub(x, x, y, n);
        else if constexpr (Op == 2) Mul(x, x, y, n);
        else Div(x, x, y, n);
        PipeBarrier<PIPE_V>();
    }
    Complete();
}

template<int Threads, int Op>
__aicore__ inline void InvokeSimt(LocalTensor<float> x, LocalTensor<float> y,
                                uint32_t n, uint32_t steps) {
    Simt::VF_CALL<SimtVF<Threads, Op>>(Simt::Dim3{Threads, 1, 1},
        reinterpret_cast<__ubuf__ float*>(x.GetPhyAddr()),
        reinterpret_cast<__ubuf__ float*>(y.GetPhyAddr()), n, steps);
}

template<int Op>
__aicore__ inline void Dispatch(uint32_t impl, uint32_t threads, LocalTensor<float> x,
                              LocalTensor<float> y, uint32_t n, uint32_t steps) {
    auto xp = reinterpret_cast<__ubuf__ float*>(x.GetPhyAddr());
    auto yp = reinterpret_cast<__ubuf__ float*>(y.GetPhyAddr());
    if (impl == 0) { TensorWork<Op>(x, y, n, steps); return; }
    if (impl == 2) VF_CALL<RegVF<Op, 1>>(xp, yp, n, steps, GetVecLen()/sizeof(float));
    else if (impl == 3) VF_CALL<RegVF<Op, 4>>(xp, yp, n, steps, GetVecLen()/sizeof(float));
    else {
        switch (threads) {
#define CASE(T) case T: InvokeSimt<T, Op>(x, y, n, steps); break
            CASE(1); CASE(32); CASE(128); CASE(512); CASE(1024); CASE(2048);
#undef CASE
        }
    }
    Complete();
}

extern "C" __global__ __aicore__ void arithmetic_kernel(
    GM_ADDR input, GM_ADDR operands, GM_ADDR output, GM_ADDR timer,
    uint32_t impl, uint32_t op, uint32_t n, uint32_t threads, uint32_t steps, uint32_t calls) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    const uint32_t padded = (n + 63) / 64 * 64;
    TPipe pipe;
    TBuf<TPosition::VECCALC> xb, yb, tb;
    pipe.InitBuffer(tb, 32);
    pipe.InitBuffer(xb, padded * sizeof(float)); pipe.InitBuffer(yb, padded * sizeof(float));
    auto x = xb.Get<float>(), y = yb.Get<float>(); auto time = tb.Get<uint64_t>();
    GlobalTensor<float> gx, gy, out; GlobalTensor<uint64_t> ticks;
    gx.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(input));
    gy.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(operands));
    out.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(output));
    ticks.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(timer));
    DataCopy(x, gx, padded); DataCopy(y, gy, padded);
    SetFlag<HardEvent::MTE2_V>(EVENT_ID0); WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0); WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    const uint64_t start = GetSystemCycle();
    switch (op) {
        case 0: Dispatch<0>(impl, threads, x, y, n, steps); break;
        case 1: Dispatch<1>(impl, threads, x, y, n, steps); break;
        case 2: Dispatch<2>(impl, threads, x, y, n, steps); break;
        case 3: Dispatch<3>(impl, threads, x, y, n, steps); break;
    }
    const uint64_t end = GetSystemCycle();
    time.SetValue(0, start); time.SetValue(1, end);
    time.SetValue(2, 0x414b4c53494d5431ULL); time.SetValue(3, n);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0); WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0); WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    DataCopy(out, x, padded); DataCopy(ticks, time, 4);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0); WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}

extern "C" void launch_arithmetic(void* stream, void* x, void* rhs, void* out, void* ticks,
    uint32_t impl, uint32_t op, uint32_t n, uint32_t threads, uint32_t steps, uint32_t calls) {
    const uint32_t padded = (n + 63) / 64 * 64;
    arithmetic_kernel<<<1, 2*padded*sizeof(float)+32, stream>>>(static_cast<uint8_t*>(x),
        static_cast<uint8_t*>(rhs), static_cast<uint8_t*>(out), static_cast<uint8_t*>(ticks),
        impl, op, n, threads, steps, calls);
}
