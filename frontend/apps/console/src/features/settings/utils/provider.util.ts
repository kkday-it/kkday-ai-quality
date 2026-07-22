// model 版本門檻判定。
/**
 * model id 是否達到最低版本門檻。僅 `gpt-N.M` 受限（gpt-5.4-mini → [5,4]）；
 * 非 gpt-* model（gemini / bytedance 等）一律放行，避免被版本規則誤濾。
 *
 * @param id model id，如 `gpt-5.4-mini`
 * @param minVersion 門檻字串，如 `5.4`
 */
export function modelMeetsMin(id: string, minVersion: string): boolean {
  const m = /^gpt-(\d+)(?:\.(\d+))?/.exec(id);
  if (!m) return true; // 非 gpt-* 不受版本限制
  const cur: [number, number] = [Number(m[1]), Number(m[2] ?? 0)];
  const [mj, mn = 0] = minVersion.split('.').map(Number);
  return cur[0] > mj || (cur[0] === mj && cur[1] >= mn);
}
