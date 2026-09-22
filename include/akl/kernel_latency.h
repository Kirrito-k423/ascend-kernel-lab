#pragma once
#include "akl/latency.h"
#include <array>
#include <cmath>
#include <cstdlib>
#include <limits>

namespace akl::latency {
inline thread_local double kernel_clock_hz = 0;
// 频率必须来自目标芯片的 SYS_CNT 说明，不能使用 AI Core 主频或可视化默认值。
inline void BeginKernelLatency(uint32_t rank, bool synchronize) {
    const char* mode = std::getenv("AKL_LATENCY_MODE");
    const std::string measurement = mode ? mode : "kernel";
    double hz = 0;
    if (measurement == "kernel") {
        const char* value = std::getenv("AKL_LATENCY_CLOCK_HZ");
        char* end = nullptr;
        hz = value ? std::strtod(value, &end) : 0;
        if (!value || end == value || *end || !std::isfinite(hz) || hz <= 0)
            throw std::invalid_argument("set AKL_LATENCY_CLOCK_HZ to the documented SYS_CNT frequency");
    } else if (measurement != "event") {
        throw std::invalid_argument("AKL_LATENCY_MODE must be kernel or event");
    }
    BeginLatency(rank, synchronize);
    kernel_clock_hz = hz;
}

// 每个 block 独占 32B：start、end、block ID、提交标记；整数相减后再换算。
inline float KernelMilliseconds(const std::vector<std::array<uint64_t, 4>>& rows, double hz) {
    if (rows.empty() || !std::isfinite(hz) || hz <= 0) throw std::invalid_argument("invalid kernel clock");
    uint64_t longest = 0;
    for (size_t block = 0; block < rows.size(); ++block) {
        const auto& row = rows[block];
        if (row[3] != 1 || row[2] != block || row[1] < row[0])
            throw std::runtime_error("incomplete kernel latency or clock rollback");
        longest = std::max(longest, row[1] - row[0]);
    }
    const double ms = double(longest) * 1000.0 / hz;
    if (!std::isfinite(ms) || ms > std::numeric_limits<float>::max())
        throw std::overflow_error("kernel latency overflow");
    return static_cast<float>(ms);
}

class KernelLatencyTimer {
public:
    KernelLatencyTimer(uint32_t rank, uint32_t blocks, void* stream)
        : events_(rank), stream_(stream), hz_(latency_session ? kernel_clock_hz : 0) {
        if (!hz_) return;
        if (!blocks) throw std::invalid_argument("kernel latency requires blocks");
        rows_.resize(blocks);
        CheckAcl(aclrtMalloc(&data_, Bytes(), ACL_MEM_MALLOC_HUGE_FIRST), "allocate kernel latency");
        try { CheckAcl(aclrtMemset(data_, Bytes(), 0, Bytes()), "zero kernel latency"); }
        catch (...) { (void)aclrtFree(data_); data_ = nullptr; throw; }
    }
    KernelLatencyTimer(const KernelLatencyTimer&) = delete;
    KernelLatencyTimer& operator=(const KernelLatencyTimer&) = delete;
    ~KernelLatencyTimer() {
        // 异常路径也必须等待 kernel 停止写入；同步失败宁可不释放设备地址。
        if (data_ && aclrtSynchronizeStream(stream_) == ACL_SUCCESS) (void)aclrtFree(data_);
    }
    uint8_t* Data() const { return static_cast<uint8_t*>(data_); }
    void Start(void* stream) { events_.Start(stream); }
    void Finish(void* stream) {
        events_.Finish(stream);
        if (!data_) return;
        CheckAcl(aclrtMemcpy(rows_.data(), Bytes(), data_, Bytes(), ACL_MEMCPY_DEVICE_TO_HOST), "read kernel latency");
        // 未启用会话不分配/不读时钟；event 模式继续返回包含屏障的整段耗时。
        latency_session->milliseconds.back() = KernelMilliseconds(rows_, hz_);
    }
private:
    size_t Bytes() const { return rows_.size() * sizeof(rows_[0]); }
    LatencyTimer events_;
    void* stream_;
    double hz_;
    void* data_{nullptr};
    std::vector<std::array<uint64_t, 4>> rows_;
};
}  // namespace akl::latency
