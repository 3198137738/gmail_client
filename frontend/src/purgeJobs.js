// 批量永久删除屏蔽地址邮件的后台任务进度（全局右下角卡片显示）。
// 进度来自实时推送 purge_progress；刷新页面、实时连接重连或实时推送不可用时从接口取回。
import { reactive } from "vue";
import { get, post } from "./api";

export const purgeJobs = reactive({ list: [] });

export function upsertPurgeJob(job) {
  const i = purgeJobs.list.findIndex((j) => j.id === job.id);
  if (i < 0) purgeJobs.list.push(job);
  else if (job.version >= purgeJobs.list[i].version) purgeJobs.list[i] = job;   // 忽略迟到的旧快照
}

export async function loadPurgeJobs() {
  try {
    const result = await get("/api/senders/purge-jobs");
    purgeJobs.list = result.jobs;
  } catch {
    /* 下次重连或轮询时再取 */
  }
}

export async function dismissPurgeJob(id) {
  purgeJobs.list = purgeJobs.list.filter((j) => j.id !== id);
  try {
    await post(`/api/senders/purge-jobs/${id}/dismiss`);
  } catch {
    /* 关闭提示失败不影响使用，刷新后可能再次出现 */
  }
}
