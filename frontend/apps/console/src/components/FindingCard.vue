<script setup lang="ts">
import { patchStatus } from '../api/client';

const props = defineProps<{ f: any }>();

const VLABEL: Record<string, string> = {
  real_config_issue: '設定錯誤', content_missing: '缺漏', content_unclear: '模糊',
  customer_misread: '客戶誤解', escalate_ops: '非內容',
};
const VCOLOR: Record<string, string> = {
  real_config_issue: 'magenta', content_missing: 'red', content_unclear: 'orange',
  customer_misread: 'gray', escalate_ops: 'blue',
};
const FLABEL: Record<string, string> = {
  prod_name: '商品名稱', prod_summary: '商品說明', prod_feature: '商品特色',
  prod_schedules: '行程', pkg_desc: '套餐使用說明', pkg_schedules: '方案行程', none: '（未定位欄位）',
};
const ALABEL: Record<string, string> = {
  fix_contradiction: '修正矛盾', add_missing_info: '補充缺漏', clarify_wording: '改寫釐清',
  no_action: '無需動作', escalate_ops: '轉其他單位', escalate_ux: 'UX 議題',
};
const CHANNEL: Record<string, string> = {
  A_platform: '平台主動', B_customer: '客人進線', C_supplier: '供應商申訴', unknown: '其他',
};

const setStatus = async (s: string) => {
  await patchStatus(props.f.finding_id, s);
  props.f.status = s;
};
</script>

<template>
  <a-card class="fcard">
    <a-space wrap style="margin-bottom: 4px">
      <a-tag color="arcoblue">{{ f.dimension }}</a-tag>
      <a-tag :color="VCOLOR[f.verdict]">{{ VLABEL[f.verdict] || f.verdict }}</a-tag>
      <a-tag v-if="f.suspected_field && f.suspected_field !== 'none'" color="purple">{{ FLABEL[f.suspected_field] }}</a-tag>
      <a-tag v-if="f.is_primary" color="purple" bordered>主要</a-tag>
      <span class="muted">信心 {{ Number(f.confidence ?? 0).toFixed(2) }}</span>
      <a-tag :color="f.status === 'confirmed' ? 'green' : f.status === 'fixed' ? 'cyan' : f.status === 'dismissed' ? 'gray' : undefined">{{ f.status }}</a-tag>
      <span v-if="f.prod_oid" class="muted mono" style="margin-left: auto">prod {{ f.prod_oid }}</span>
    </a-space>

    <div class="summary">{{ f.problem_summary }}</div>
    <div v-if="f.evidence_quote" class="quote">📄 目前頁面：{{ f.evidence_quote }}</div>
    <div v-if="f.ground_truth_quote" class="gt"><b>✅ 客服標準答案（待補事實）：</b>{{ f.ground_truth_quote }}</div>

    <div class="meta-row">
      <span class="chip">📥 感知層：{{ CHANNEL[f.source_channel] || '其他' }}<template v-if="f.source_system"> · {{ f.source_system }}</template></span>
      <span v-if="f.owner_role" class="chip">👤 {{ f.owner_role }}</span>
      <span v-if="f.exec_platform" class="chip">🛠 {{ f.exec_platform }}</span>
    </div>

    <div class="actions">
      <span v-if="f.verdict === 'customer_misread'" class="muted">內容其實清楚 → 不需修改（呈現/UX 議題）</span>
      <span v-else-if="f.verdict === 'escalate_ops'" class="muted">非內容問題 → 轉其他單位</span>
      <template v-else-if="f.writer_handoff">
        <a-button size="mini" type="primary">✎ 用 writer 重生{{ FLABEL[f.suspected_field] }}</a-button>
        <span class="muted">可重生（改寫既有事實），結果供確認不自動寫回</span>
      </template>
      <span v-else class="muted warn">⛔ 缺事實 → 需 PM 手動補（不可自動重生）</span>
    </div>

    <div class="actions">
      <span class="muted">建議動作：<b>{{ ALABEL[f.recommended_action] || f.recommended_action }}</b></span>
      <span style="margin-left: auto"></span>
      <a-button size="mini" type="outline" status="success" @click="setStatus('confirmed')">確認</a-button>
      <a-button size="mini" type="outline" @click="setStatus('dismissed')">忽略</a-button>
      <a-button size="mini" type="outline" status="warning" @click="setStatus('fixed')">已修</a-button>
    </div>
  </a-card>
</template>

<style scoped>
.fcard { margin-bottom: 12px; }
.summary { font-size: 14px; line-height: 1.55; margin: 4px 0; }
.quote { color: #86909c; font-size: 12.5px; margin-top: 6px; line-height: 1.5; }
.gt { background: #e8fffb; border: 1px solid #a3e8dd; border-radius: 8px; padding: 8px 11px; margin-top: 8px; font-size: 12.5px; line-height: 1.5; }
.gt b { color: #0f9b8e; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
.muted { color: #86909c; font-size: 12px; }
.muted.warn { color: #fb923c; }
.mono { font-family: ui-monospace, monospace; }
.meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 11.5px; color: #4e5969; background: #f2f3f5; border-radius: 6px; padding: 2px 8px; }
</style>
