#pragma once

#include <acl/acl.h>
#include <cstdint>
#include <array>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>
#include <memory>

namespace akl::latency {
inline void CheckAcl(aclError status, const char* operation) {
    if (status != ACL_SUCCESS)
        throw std::runtime_error(std::string(operation) + " ACL status=" + std::to_string(status));
}

// 每个 Host 线程显式启用一次会话；复用设备事件，逐轮只暂存毫秒样本。
// 调用方在 Start/Finish 外安排屏障、数据导出，并在同一线程/设备结束会话。
struct LatencySession {
    uint32_t rank;
    bool synchronize_start;
    aclrtEvent start{nullptr};
    aclrtEvent end{nullptr};
    std::vector<float> milliseconds;
    std::vector<std::array<uint64_t, 2>> work_bytes;

    LatencySession(uint32_t rank_id, bool synchronize) : rank(rank_id), synchronize_start(synchronize) {
        try {
            CheckAcl(aclrtCreateEventWithFlag(&start, ACL_EVENT_TIME_LINE), "create start event");
            CheckAcl(aclrtCreateEventWithFlag(&end, ACL_EVENT_TIME_LINE), "create end event");
            milliseconds.reserve(1024);
        } catch (...) { Release(); throw; }
    }
    LatencySession(const LatencySession&) = delete;
    LatencySession& operator=(const LatencySession&) = delete;
    ~LatencySession() { Release(); }
    void Release() noexcept {
        if (end) { (void)aclrtDestroyEvent(end); end = nullptr; }
        if (start) { (void)aclrtDestroyEvent(start); start = nullptr; }
    }
};
inline thread_local std::unique_ptr<LatencySession> latency_session;

inline void BeginLatency(uint32_t rank, bool synchronize) {
    if (latency_session) throw std::runtime_error("nested latency sessions are not supported");
    latency_session = std::make_unique<LatencySession>(rank, synchronize);
}
inline void AbortLatency() noexcept {
    latency_session.reset();
}
inline size_t LatencyCount() {
    if (!latency_session) throw std::runtime_error("no latency session on this thread");
    return latency_session->milliseconds.size();
}
// 每个样本两列：处理的有效字节、跨 rank 发送的有效字节；必须在 EndLatency 前取出。
inline void CopyWorkBytes(uint64_t* output, size_t count) {
    if (count != LatencyCount() || latency_session->work_bytes.size() != count || (count && !output))
        throw std::invalid_argument("incomplete work byte samples");
    for (size_t i = 0; i < count; ++i)
        std::copy_n(latency_session->work_bytes[i].data(), 2, output + 2 * i);
}

inline void EndLatency(float* samples, size_t count) {
    if (count != LatencyCount() || (count && !samples))
        throw std::invalid_argument("invalid latency output buffer");
    try {
        std::copy(latency_session->milliseconds.begin(), latency_session->milliseconds.end(), samples);
    } catch (...) { AbortLatency(); throw; }
    AbortLatency();
}

class LatencyTimer {
public:
    explicit LatencyTimer(uint32_t rank) : session_(latency_session.get()) {
        if (session_ && session_->rank != rank)
            throw std::runtime_error("profiling session rank does not match measurement rank");
    }
    bool SynchronizeStart() const { return session_ && session_->synchronize_start; }
    void Start(void* stream) {
        if (session_) CheckAcl(aclrtRecordEvent(session_->start, stream), "record start event");
    }
    void Finish(void* stream) {
        if (!session_) return;
        CheckAcl(aclrtRecordEvent(session_->end, stream), "record end event");
        CheckAcl(aclrtSynchronizeEvent(session_->end), "synchronize end event");
        float ms = 0;
        CheckAcl(aclrtEventElapsedTime(&ms, session_->start, session_->end), "event elapsed time");
        session_->milliseconds.push_back(ms);
    }
private:
    LatencySession* session_;
};
}  // namespace akl::latency
