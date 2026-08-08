<script setup lang="ts">
/**
 * 待審建議對比抽屜：左「人工現值」右「LLM 新值」，逐條採納或駁回。
 *
 * 兩側同形（後端就把 current / proposed 都做成 attribution_dto 形狀），所以這裡用**同一個**
 * 描述區塊渲染函式跑兩欄——這是省最多前端程式碼的一個決定。
 *
 * 窄容器內不用 `:scroll="{x}"` 硬撐多欄，改以「左小標籤＋右內容」的描述區塊收斂（frontend-vue.md
 * 的窄容器表格強制條款）。
 */
import { computed, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { IconCheckCircle, IconCloseCircle } from '@arco-design/web-vue/es/icon';
import { type PendingSuggestions, type SuggestionItem, getSuggestions, resolveSuggestions } from '@/api';
import { AsyncSection, TableLayout } from '@/components';
import { CHANGE_TYPE_COLORS, CHANGE_TYPE_LABELS } from '../constants';
import { attributionLines as lines } from '../utils';

const props = defineProps<{ visible: boolean; source: string; sourceId: string }>();
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  /** 有任何採納/駁回發生 → 通知列表重新載入。 */
  (e: 'resolved'): void;
}>();

const data = ref<PendingSuggestions | null>(null);
const loading = ref(false);
const error = ref('');
const busy = ref(false);

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    data.value = await getSuggestions(props.source, props.sourceId);
  } catch (e: unknown) {
    error.value = (e as Error)?.message || '載入待審建議失敗';
  } finally {
    loading.value = false;
  }
};

watch(
  () => [props.visible, props.sourceId] as const,
  ([v]) => {
    if (v && props.sourceId) void load();
  },
  { immediate: true },
);

const items = computed(() => data.value?.items ?? []);

/** 一條歸因 → 對比欄要顯示的三段（分類 / 傾向 / 信心）；current 與 proposed 共用。 */
// `lines()` 已抽到 utils/attribution.util 的 `attributionLines`（糾正工作台是第二個消費端，
// 依「第 2 次出現即抽」的規則抽出）——兩處對同一條歸因的讀法必須一致。

const _resolve = async (decisions: SuggestionItem[], decision: 'accept' | 'reject', reason = '') => {
  if (!data.value?.batch_id) return;
  busy.value = true;
  try {
    const r = await resolveSuggestions({
      source: props.source,
      source_id: props.sourceId,
      batch_id: data.value.batch_id,
      decisions: decisions.map((i) => ({ suggestion_oid: i.suggestion_oid, decision })),
      reason,
    });
    Message.success(`已採納 ${r.applied} 條、駁回 ${r.rejected} 條`);
    emit('resolved');
    await load();
    if (!items.value.length) emit('update:visible', false);
  } catch (e: unknown) {
    Message.error((e as Error)?.message || '處理失敗');
  } finally {
    busy.value = false;
  }
};

const accept = (rows: SuggestionItem[]) => _resolve(rows, 'accept');

/** 駁回＝主張 AI 錯，與人工糾正同級，故鼓勵填理由（理由輸入放抽屜內，確認窗只做純文字確認）。 */
const rejectReason = ref('');

const reject = (rows: SuggestionItem[]) => {
  Modal.confirm({
    title: rows.length > 1 ? `駁回全部 ${rows.length} 條建議？` : '駁回這條建議？',
    content: '駁回後現值保持不變，這些建議會消失。下次重新初判若結論相同，會再次提出。',
    okText: '駁回',
    cancelText: '取消',
    okButtonProps: { status: 'warning' },
    onOk: () => _resolve(rows, 'reject', rejectReason.value.trim()),
  });
};

const columns = [
  { title: '變更', dataIndex: 'change_type', width: 84, slotName: 'change' },
  { title: '人工現值', slotName: 'current', width: 240 },
  { title: 'LLM 新值', slotName: 'proposed', width: 240 },
  { title: '操作', slotName: 'ops', width: 128 },
];
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="900"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '12px 16px' }"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>
      <span>待審 LLM 建議</span>
      <a-tag v-if="data?.model" size="small" color="purple" class="ml-2">{{ data.model }}</a-tag>
      <a-tag v-if="items.length" size="small" class="ml-1">{{ items.length }} 條</a-tag>
    </template>

    <a-alert type="info" class="mb-2">
      這則反饋已被人工糾正過，所以重新初判的結果**沒有覆蓋現值**，而是列在這裡等你決定。
      採納後現值更新、但仍維持人工託管（下次重判一樣不會直接覆蓋）。
    </a-alert>

    <AsyncSection :loading="loading" :error="error" :empty="!items.length" empty-text="目前沒有待審建議">
      <div class="min-h-0 flex-1 overflow-hidden">
        <TableLayout
          full-height
          :data="items"
          :columns="columns"
          :pagination="false"
          row-key="suggestion_oid"
        >
          <template #change="{ record }">
            <a-tag size="small" :color="CHANGE_TYPE_COLORS[record.change_type]">
              {{ CHANGE_TYPE_LABELS[record.change_type] || record.change_type }}
            </a-tag>
          </template>
          <template #current="{ record }">
            <div v-if="record.current" class="flex flex-col gap-1 text-xs">
              <div v-for="l in lines(record.current)" :key="l.k" class="flex gap-1.5">
                <span class="shrink-0 text-[var(--color-text-3)]">{{ l.k }}</span>
                <span class="min-w-0 truncate" :title="l.v">{{ l.v }}</span>
              </div>
            </div>
            <span v-else class="text-[var(--color-text-3)]">—（AI 新發現的面向）</span>
          </template>
          <template #proposed="{ record }">
            <div v-if="record.change_type !== 'remove'" class="flex flex-col gap-1 text-xs">
              <div v-for="l in lines(record.proposed)" :key="l.k" class="flex gap-1.5">
                <span class="shrink-0 text-[var(--color-text-3)]">{{ l.k }}</span>
                <span class="min-w-0 truncate font-medium" :title="l.v">{{ l.v }}</span>
              </div>
            </div>
            <span v-else class="text-[var(--color-text-3)]">—（AI 認為此面向不再成立）</span>
          </template>
          <template #ops="{ record }">
            <div class="flex flex-wrap gap-1">
              <a-button type="text" size="mini" :disabled="busy" @click="accept([record])">
                <template #icon><icon-check-circle /></template>
                採納
              </a-button>
              <a-button
                type="text"
                size="mini"
                status="danger"
                :disabled="busy"
                @click="reject([record])"
              >
                <template #icon><icon-close-circle /></template>
                駁回
              </a-button>
            </div>
          </template>
        </TableLayout>
      </div>

      <div class="mt-2 flex flex-col gap-1">
        <a-input
          v-model="rejectReason"
          size="small"
          allow-clear
          placeholder="駁回理由（選填）：例如「AI 仍未讀到退款對話」"
        />
        <span class="text-xs text-[#86909c]">
          理由會記進這則反饋的時間軸，供日後追溯「為什麼當時沒採納 AI 的結論」。
        </span>
      </div>
      <div class="mt-2 flex justify-end gap-2">
        <a-button type="outline" status="warning" :disabled="busy" @click="reject(items)">
          <template #icon><icon-close-circle /></template>
          全部駁回
        </a-button>
        <a-button type="primary" :loading="busy" @click="accept(items)">
          <template #icon><icon-check-circle /></template>
          全部採納
        </a-button>
      </div>
    </AsyncSection>
  </a-drawer>
</template>
