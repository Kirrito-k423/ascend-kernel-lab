#pragma once
#include "shmem.h"

// 必要的基准适配：多个 SQ 可能共享同一实际 CQ；按 QP 分别消费会误收其他 SQ 的完成。
// 仅用于此基准的单 AIV 独占 peer、独立 GET/PUT WQE；不修改 SDK，也不支持多 AIV 并发消费。
ACLSHMEM_DEVICE bool AklSameCq(__gm__ aclshmemi_udma_cq_ctx_t* a,
    __gm__ aclshmemi_udma_cq_ctx_t* b) {
    return a->cqn==b->cqn && a->buf_addr==b->buf_addr && a->db_addr==b->db_addr;
}
ACLSHMEM_DEVICE uint32_t AklQuiet(uint32_t peer,uint32_t qp,bool grouped) {
    static_assert(!ACLSHMEM_RELAY_SUPPORTED,"QP benchmark requires direct UDMA");
    auto info=aclshmemi_udma_qp_info_fetch();auto table=aclshmemi_udma_active_table(info);
    auto queues=reinterpret_cast<__gm__ aclshmemi_udma_cq_ctx_t*>(table->scq_ptr)+peer*info->qp_num;
    auto sends=reinterpret_cast<__gm__ aclshmemi_udma_wq_ctx_t*>(table->sq_ptr)+peer*info->qp_num;
    auto cq=queues+qp;uint32_t count=0,target=0,canonical=qp;
    if(grouped)for(uint32_t q=0;q<info->qp_num;++q)if(AklSameCq(cq,queues+q)){
        ++count;target+=sends[q].cqe_cnt;if(q<canonical)canonical=q;
    }
    if(count<2)return aclshmemi_udma_poll_cq(peer,qp,sends[qp].cqe_cnt);
    // 共享 CQ 只消费一次，总目标是组内所有 SQ 的 CQE 计数；未使用的 SQ 贡献零。
    cq=queues+canonical;uint32_t cursor=cq->tail;
    while(cursor!=target){
        auto entry=reinterpret_cast<__gm__ aclshmemi_jfc_cqe_ctx_t*>(cq->buf_addr+
            uint64_t(cq->cqe_size)*(cursor%cq->depth));
        const bool owner=(cursor/cq->depth)&1;uint32_t retries=0;
        while((owner^entry->owner)==0 && retries<MAX_RETRY_TIMES){
            dcci_cachelines(reinterpret_cast<__gm__ uint8_t*>(entry),sizeof(aclshmemi_jfc_cqe_ctx_t));++retries;
        }
        if(retries==MAX_RETRY_TIMES)return 0xFF;
        if(entry->status || entry->substatus)return (entry->status<<8)|entry->substatus;
        // CQE 的 20 位本地队列号标识实际 SQ，而非 quiet 调用者的 QP。
        const uint32_t identity=(entry->local_num_h<<16)|entry->local_num_l;
        uint32_t found=info->qp_num;
        for(uint32_t q=0;q<info->qp_num;++q)
            if(AklSameCq(cq,queues+q) && sends[q].wqn==identity){found=q;break;}
        if(found==info->qp_num)return 0x10001;
        auto w=sends+found;
        // entry_idx 是最后完成的 64B BB，故 +1；按 16 位模数恢复到不超过 head 的最新计数。
        // 每批最多 128 次发送，先 quiet 再复用；未完成区间远小于此模数，不能把 SQ 深度当模数。
        const uint32_t mask=ACLSHMEM_UDMA_CQE_ENTRY_IDX_MOD-1;
        uint32_t completed=(w->head&~mask)|((entry->entry_idx+1)&mask);
        if(completed>w->head)completed-=ACLSHMEM_UDMA_CQE_ENTRY_IDX_MOD;
        if(completed<w->tail || completed>w->head)return 0x10002;
        w->tail=completed;++cursor;
        for(uint32_t q=0;q<info->qp_num;++q)if(AklSameCq(cq,queues+q))queues[q].tail=cursor;
    }
    st_dev(cursor&0xFFFFFF,reinterpret_cast<__gm__ uint32_t*>(cq->db_addr),0);
    return 0;
}
ACLSHMEM_DEVICE uint32_t AklQuietPeer(uint32_t qps,bool grouped) {
    for(uint32_t q=0;q<qps;++q){const uint32_t error=AklQuiet(1,q,grouped);if(error)return error;}
    return 0;
}
