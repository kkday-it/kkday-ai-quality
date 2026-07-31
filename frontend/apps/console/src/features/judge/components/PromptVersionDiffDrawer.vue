<script setup lang="ts">
/**
 * 版本對比抽屜（巢狀於「版本列表」之上）。
 *
 * 為什麼獨立成一層抽屜、而不是內嵌在版本列表裡：Prompt 全文約 105KB／千餘行，內嵌 diff 會把
 * 版本列表整個推到捲軸深處——實測要捲很久才看得到列表本體，而列表才是那個抽屜的主體。
 * 拆一層之後，關掉對比就回到列表原位，列表的篩選與捲動位置完全不受影響。
 *
 * 為什麼「選哪兩版」放在本抽屜內、而不是列表每列給選取鈕：47 列各掛選取鈕是純視覺噪音，
 * 且要比對得先在列表點兩次再點對比＝三步。改成在此用兩個下拉即時切換，比對多組時尤其省事。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getPromptDraft,
  getPromptRelease,
  type PromptDraftMeta,
  type PromptReleaseMeta,
} from '@/api';
import { MdTextDiff } from '@/components';

const props = defineProps<{
  visible: boolean;
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
  /** 打開時的初始基準；`kind:name` 形式（如 `release:release-v1`）。可為空。 */
  initialA?: string;
  /** 打開時的初始對照。可為空。 */
  initialB?: string;
}>();
const emit = defineEmits<{ (e: 'update:visible', value: boolean): void }>();

const keyA = ref('');
const keyB = ref('');
const leftText = ref('');
const rightText = ref('');
const loading = ref(false);
/** 全文約 105KB，同一份不重複拉（換版比對時常回頭看同一份）。 */
const cache = new Map<string, string>();

/** `kind:name` → 人看的標籤（下拉與 diff 標頭共用，避免兩處各拼一次）。 */
const labelOf = (key: string): string => {
  if (!key) return '未選擇';
  const sep = key.indexOf(':');
  const kind = key.slice(0, sep);
  const name = key.slice(sep + 1);
  if (kind === 'release') {
    const hit = props.releases.find((r) => r.name === name);
    return `正式 ${name}${hit?.is_active ? '（使用中）' : ''}`;
  }
  return `草稿 ${name}`;
};

/**
 * 兩軌分組的下拉選項。
 *
 * ⚠️ Arco 的 `a-select` 要認得群組**必須帶 `isGroup: true`**——只給 `{label, options}` 會被
 * 當成一個普通選項，結果選不到任何值、且顯示的是原始 `kind:name` 字串（踩過一次）。
 */
const groups = computed(() => {
  const out: Array<{
    isGroup: true;
    label: string;
    options: Array<{ value: string; label: string }>;
  }> = [];
  if (props.releases.length) {
    out.push({
      isGroup: true,
      label: `正式版（${props.releases.length}）`,
      options: props.releases.map((r) => ({
        value: `release:${r.name}`,
        label: r.is_active ? `${r.name}（使用中）` : r.name,
      })),
    });
  }
  if (props.drafts.length) {
    out.push({
      isGroup: true,
      label: `草稿（${props.drafts.length}）`,
      options: props.drafts.map((d) => ({ value: `draft:${d.version}`, label: d.version })),
    });
  }
  return out;
});

/** 所有可選 key（依序：正式版新→舊、草稿新→舊），供「初始值缺漏時自動補」用。 */
const allKeys = computed(() => [
  ...props.releases.map((r) => `release:${r.name}`),
  ...props.drafts.map((d) => `draft:${d.version}`),
]);

async function fetchText(key: string): Promise<string> {
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  const sep = key.indexOf(':');
  const [kind, name] = [key.slice(0, sep), key.slice(sep + 1)];
  const res = kind === 'release' ? await getPromptRelease(name) : await getPromptDraft(name);
  cache.set(key, res.system_prompt);
  return res.system_prompt;
}

const ready = computed(() => !!keyA.value && !!keyB.value && keyA.value !== keyB.value);

async function loadDiff(): Promise<void> {
  if (!ready.value) {
    leftText.value = '';
    rightText.value = '';
    return;
  }
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchText(keyA.value), fetchText(keyB.value)]);
    leftText.value = a;
    rightText.value = b;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '載入版本全文失敗');
    leftText.value = '';
    rightText.value = '';
  } finally {
    loading.value = false;
  }
}

/**
 * 開抽屜時決定初始兩版。initialA/B 可能為空或指向已不存在的版本（清單重載過），
 * 一律回退到「第一個可選」與「第一個不同於基準的可選」——**開起來一定是可比的兩版**，
 * 不讓使用者看到空白下拉還得自己猜要選什麼。
 */
function resolveInitial(): void {
  const keys = allKeys.value;
  const valid = (k?: string): string => (k && keys.includes(k) ? k : '');
  const a = valid(props.initialA) || keys[0] || '';
  const b = valid(props.initialB) !== a ? valid(props.initialB) : '';
  keyA.value = a;
  keyB.value = b || keys.find((k) => k !== a) || '';
}

watch([keyA, keyB], loadDiff);
watch(
  () => props.visible,
  (open) => {
    if (!open) return;
    resolveInitial();
    void loadDiff();
  },
  { immediate: true },
);

/** 互換兩側（同一組差異換個方向看，比重新選兩次快）。 */
function swap(): void {
  [keyA.value, keyB.value] = [keyB.value, keyA.value];
}
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="1100"
    title="版本對比"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <!-- 選版就在這裡切，不必回列表 -->
    <a-row :gutter="[8, 8]" align="center" wrap class="mb-3">
      <a-col flex="none"><span class="text-xs text-[#86909c]">基準</span></a-col>
      <a-col flex="300px">
        <a-select
          v-model="keyA"
          class="w-full"
          size="small"
          :options="groups"
          placeholder="選擇基準版本"
        />
      </a-col>
      <a-col flex="none"><span class="text-xs text-[#86909c]">→ 對照</span></a-col>
      <a-col flex="300px">
        <a-select
          v-model="keyB"
          class="w-full"
          size="small"
          :options="groups"
          placeholder="選擇對照版本"
        />
      </a-col>
      <a-col flex="none">
        <a-button size="small" type="text" :disabled="!ready" @click="swap">⇄ 互換</a-button>
      </a-col>
    </a-row>

    <a-alert v-if="keyA && keyA === keyB" type="warning" class="mb-2">
      兩側選了同一版，請換一邊
    </a-alert>

    <a-spin v-if="loading" class="w-full py-10 text-center" tip="載入全文中…" />
    <a-empty v-else-if="!ready" class="py-10" description="請在上方選擇要對比的兩個版本" />
    <div v-else class="min-h-0 flex-1 overflow-auto">
      <MdTextDiff
        :old-text="leftText"
        :new-text="rightText"
        :old-label="labelOf(keyA)"
        :new-label="labelOf(keyB)"
      />
    </div>
  </a-drawer>
</template>
