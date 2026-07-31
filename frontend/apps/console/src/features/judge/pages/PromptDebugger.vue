<script setup lang="ts">
import {
  getPromptDebugDefaults,
  getPromptDraft,
  getPromptRelease,
  savePromptDraft,
  streamPromptDebug,
  type PromptDebugDefaults,
  type PromptDebugMeta,
  type PromptDebugResult,
  type PromptDebugUsage,
} from '@/api';
import { LlmConfigSelect } from '@/components';
import { Message, Modal } from '@arco-design/web-vue';
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useLlmAreaConfig } from '@/composables';
// 評判區塊跟著判決結果一起出現，lazy 只會讓結果到齊的瞬間閃一下，故靜態載入
import PromptReviewPanel from '../components/PromptReviewPanel.vue';

// 跑批抽屜點開才載（重元件 lazy，非首屏必需）
const PromptDebugBatchDrawer = defineAsyncComponent(
  () => import('../components/PromptDebugBatchDrawer.vue'),
);
// 案例庫／AI 改寫抽屜同理，點開才載
const PromptReviseDrawer = defineAsyncComponent(
  () => import('../components/PromptReviseDrawer.vue'),
);
// 版本面板（草稿列表 + diff + 升版）同理，點開才載
const PromptVersionDrawer = defineAsyncComponent(
  () => import('../components/PromptVersionDrawer.vue'),
);

const router = useRouter();
const route = useRoute();
const llm = useLlmAreaConfig('prompt_debug');

const defaults = ref<PromptDebugDefaults | null>(null);
const systemPrompt = ref('');
const inputText = ref('');
const loadingDefaults = ref(false);
const savingVersion = ref(false);

/**
 * 編輯器內容當前對應的**已存版本**基準：
 * - `text`＝那份版本的全文，用來判斷「有沒有再被編輯」（不能拿固定某軌硬比——存完草稿後編輯器
 *   裝的是草稿內容，與 active release 本來就不同，那不叫「未存檔」）
 * - `name`＝那份版本的名字（草稿時間戳或正式版名）
 */
const baseline = ref<{ text: string; name: string }>({ text: '', name: '' });

/**
 * 本頁口徑：`draft`＝最新草稿（預設）｜`release`＝當前正式版。
 *
 * **這是本頁唯一的口徑來源**——編輯器載入、單次測試、跑批三者全部跟它走。「頁面調 A、跑批跑 B」
 * 的防線是「默認值一致」，不是「限制跑批能讀什麼」（後者只會讓跑批不可用）。
 */
const track = ref<'draft' | 'release'>('draft');

/** 內容已偏離基準版本＝本次送出的是臨時 Prompt（未落任何檔）。 */
const isEdited = computed(() => !!defaults.value && systemPrompt.value !== baseline.value.text);

/** 是否有正式版可切（沒有時「正式」側 disable，但草稿工作流完全不受影響）。 */
const hasRelease = computed(() => !!defaults.value?.active_release);

const batchVisible = ref(false);
const reviseVisible = ref(false);
const versionVisible = ref(false);
/** 案例庫筆數（入口按鈕上的徽章；存新案例後 +1，開抽屜時以後端實數校正）。 */
const reviewCount = ref(0);

// ── 案例庫 × AI 改寫流水線的開合與步驟（沿用 SettingsDrawer 的 route-query 慣例）────────
/** 抽屜要開在第幾步；也是 `?revise=` 的值。 */
const reviseStep = ref(1);
/** 開抽屜時要預先勾選的案例 id（剛存完案例直接跳過去時用）。 */
const revisePreselectId = ref<number | undefined>(undefined);

const syncReviseQuery = (step: number): void => {
  void router.replace({ query: { ...route.query, revise: String(step) } });
};
const clearReviseQuery = (): void => {
  if (!route.query.revise) return; // 冪等守衛：避免無謂的 replace
  const query = { ...route.query };
  delete query.revise;
  void router.replace({ query });
};

/**
 * 開流水線抽屜。
 * @param step 想落在第幾步；抽屜內部會依當前狀態 clamp（重整後多半只能回到①）。
 * @param preselectId 要預先勾選的案例 id。
 */
function openRevise(step = 1, preselectId?: number): void {
  reviseStep.value = step;
  revisePreselectId.value = preselectId;
  reviseVisible.value = true;
  syncReviseQuery(step);
}

/** 抽屜內換步 → 同步進 query，重整/分享連結時回得來。 */
function onReviseStep(step: number): void {
  reviseStep.value = step;
  if (reviseVisible.value) syncReviseQuery(step);
}

watch(reviseVisible, (open) => {
  if (!open) {
    clearReviseQuery();
    revisePreselectId.value = undefined;
  }
});

// query → 狀態（`immediate` 兼作重整還原；也讓頁面內其他地方能用 query 遠端開抽屜）
watch(
  () => route.query.revise,
  (value) => {
    if (value === undefined || value === null) return;
    reviseStep.value = Number(Array.isArray(value) ? value[0] : value) || 1;
    reviseVisible.value = true;
  },
  { immediate: true },
);

/** 存完案例只更新徽章；要不要立刻去改寫由使用者按評判區塊那顆連結決定（見 `PromptReviewPanel`）。 */
function onCaseSaved(): void {
  reviewCount.value += 1;
}

/** ③建議改用跑批複驗：關流水線、開跑批（那裡已可直接選要跑哪一版 Prompt）。 */
function onRequestBatch(): void {
  reviseVisible.value = false;
  batchVisible.value = true;
}

const streaming = ref(false);
const rawOutput = ref('');
const result = ref<PromptDebugResult | null>(null);
const usage = ref<PromptDebugUsage | null>(null);
const meta = ref<PromptDebugMeta | null>(null);
const warnings = ref<string[]>([]);
const errorMessage = ref('');
const outputRef = ref<HTMLElement>();
let abortController: AbortController | null = null;

const canRun = computed(
  () =>
    !!llm.overrides.value.provider &&
    !!llm.overrides.value.model.trim() &&
    !!systemPrompt.value.trim() &&
    !!inputText.value.trim(),
);
const displayedResults = computed(() => {
  const parsed = result.value?.parsed;
  if (!parsed || !defaults.value) return [];
  return defaults.value.output_fields
    .filter((field) => Object.prototype.hasOwnProperty.call(parsed, field.key))
    .map((field) => ({ ...field, value: parsed[field.key] }));
});

/** 某一軌當前的全文與版本名（供載入與口徑切換共用，避免兩處各算一次）。 */
function trackSnapshot(next: 'draft' | 'release'): { text: string; name: string } {
  const d = defaults.value;
  if (!d) return { text: '', name: '' };
  return next === 'release'
    ? { text: d.release_prompt ?? '', name: d.active_release ?? '' }
    : { text: d.system_prompt ?? '', name: d.latest_draft ?? '' };
}

/** 當前軌的版本下拉選項（草稿＝時間戳新→舊；正式＝版本名，active 標註）。 */
const versionOptions = computed(() => {
  const d = defaults.value;
  if (!d) return [];
  return track.value === 'release'
    ? d.releases.map((r) => ({
        value: r.name,
        label: r.is_active ? `${r.name}（使用中）` : r.name,
      }))
    : d.drafts.map((x) => ({ value: x.version, label: x.version }));
});

const loadingVersion = ref(false);

/**
 * 切換到當前軌的指定版本：拉該版全文載進編輯器，基準一併指向它。
 *
 * 有未存改動時先二次確認——直接換版會靜默丟失編輯（與口徑切換同一道防線）。
 * @param name 版本名（草稿時間戳或正式版名）。
 */
async function selectVersion(name: string): Promise<void> {
  if (!name || name === baseline.value.name) return;
  const apply = async (): Promise<void> => {
    loadingVersion.value = true;
    try {
      const text =
        track.value === 'release'
          ? (await getPromptRelease(name)).system_prompt
          : (await getPromptDraft(name)).system_prompt;
      systemPrompt.value = text;
      baseline.value = { text, name };
    } catch (error) {
      Message.error(error instanceof Error ? error.message : `載入版本 ${name} 失敗`);
    } finally {
      loadingVersion.value = false;
    }
  };
  if (!isEdited.value) return void apply();
  Modal.confirm({
    title: '尚未存為草稿',
    content: `切換到 ${name} 會丟棄目前未存檔的編輯內容，確定切換？`,
    okText: '切換並丟棄',
    cancelText: '留在此處',
    onOk: apply,
  });
}

/**
 * 載入 defaults（最新草稿全文 + 正式版全文 + 兩軌清單）。
 * @param resetEditor 是否把編輯器內容重置為當前口徑的全文。**存完草稿後必須傳 false**——
 *   否則會把使用者剛編輯的內容沖掉（存草稿只是落檔，不該打斷手上的編輯）。
 */
async function loadDefaults(resetEditor = true): Promise<void> {
  loadingDefaults.value = true;
  try {
    defaults.value = await getPromptDebugDefaults();
    // 正式版可能不存在（草稿中心下這完全合法）→ 口徑自動落回草稿側，不讓頁面卡住
    if (track.value === 'release' && !defaults.value.active_release) track.value = 'draft';
    if (resetEditor) {
      const snap = trackSnapshot(track.value);
      systemPrompt.value = snap.text;
      baseline.value = snap;
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '載入 Prompt 失敗');
  } finally {
    loadingDefaults.value = false;
  }
}

/**
 * 切換本頁口徑（草稿 ⇄ 正式）。編輯器有未存改動時先二次確認——直接切會靜默丟失編輯。
 * @param next 目標軌。
 */
async function setTrack(next: 'draft' | 'release'): Promise<void> {
  if (next === track.value) return;
  const apply = (): void => {
    track.value = next;
    const snap = trackSnapshot(next);
    systemPrompt.value = snap.text;
    baseline.value = snap;
  };
  if (!isEdited.value) return apply();
  Modal.confirm({
    title: '尚未存為草稿',
    content: `切換到「${next === 'draft' ? '草稿' : '正式'}」會丟棄目前未存檔的編輯內容，確定切換？`,
    okText: '切換並丟棄',
    cancelText: '留在此處',
    onOk: apply,
  });
}

/** 已被升版過的草稿名集合（`releases[].source_draft`）；這些草稿不需再升一次。 */
const promotedDrafts = computed(
  () => new Set((defaults.value?.releases ?? []).map((r) => r.source_draft).filter(Boolean)),
);

/**
 * 當前選定的草稿能不能升版：必須在草稿軌、有選到版本、內容未被編輯（升版要升的是**已存檔**
 * 那一份，不是編輯器裡的未存內容）、且尚未升版過。
 */
const promotableDraft = computed(() => {
  if (track.value !== 'draft' || isEdited.value || !baseline.value.name) return '';
  return promotedDrafts.value.has(baseline.value.name) ? '' : baseline.value.name;
});

/** 升版鈕 disable 時的原因（給 tooltip，讓「為什麼不能點」可見）。 */
const promoteBlockedReason = computed(() => {
  if (track.value !== 'draft') return '正式版無需再升版';
  if (isEdited.value) return '有未存檔的編輯，請先「存為新草稿」';
  if (!baseline.value.name) return '尚未選定草稿';
  if (promotedDrafts.value.has(baseline.value.name)) return '此草稿已升版過';
  return '';
});

/** 升版走版本列表抽屜的既有確認流程（名稱建議／撞名檢查／必填理由都在那裡，不另做一套）。 */
function openPromoteForCurrent(): void {
  versionVisible.value = true;
  promoteTarget.value = promotableDraft.value;
}

/** 傳給版本列表抽屜：開啟時直接對這支草稿彈出升版確認（空＝不預先彈）。 */
const promoteTarget = ref('');

/**
 * 存為新草稿：寫進草稿區供對比與後續升版。**不改變線上口徑**——要上線得在版本面板
 * 對該草稿按「設為正式版」（升版是獨立的高權限動作）。
 */
async function saveVersion(): Promise<void> {
  if (savingVersion.value || !systemPrompt.value.trim()) return;
  savingVersion.value = true;
  try {
    const saved = await savePromptDraft(systemPrompt.value);
    // 只刷新清單，不重置編輯器：編輯器現在裝的就是這支草稿，基準跟著指向它
    await loadDefaults(false);
    // 存完就跟進這支新草稿：口徑留在草稿側，基準指向它（否則下一次「恢復」會跳回別的版本）
    track.value = 'draft';
    baseline.value = { text: systemPrompt.value, name: String(saved.version) };
    Message.success(
      saved.created
        ? `已存為新草稿 ${saved.version}（線上正式版未變，仍是 ${defaults.value?.active_release || '—'}）`
        : `內容與最新草稿 ${saved.version} 相同，未建立新草稿`,
    );
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '存為新草稿失敗');
  } finally {
    savingVersion.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadDefaults(), llm.loadConfigs()]);
});

const samples = [
  {
    label: '憑證未送達',
    text: '[USER] 我後天就要出發，但仍沒有收到主辦單位寄出的電子票，垃圾郵件也找過了。\n[BOT] KKday 憑證已發送，但主辦單位電子票需等待寄送。\n[USER] 請幫我查還要等多久。',
  },
  {
    label: '修改日期受限',
    text: '[USER] 我訂錯日期，想把 8/12 改成 8/13。\n[BOT] 此商品規則不支援原訂單改期，只能取消後重新下單。\n[USER] 那請問要怎麼處理？',
  },
  {
    label: 'OOT 售前詢問',
    text: '[USER] 還沒下單，請問這個行程適合帶三歲小孩嗎？現場有兒童座椅嗎？\n[BOT] 請以商品頁與供應商回覆為準。',
  },
];

/**
 * 丟棄編輯、回到**當前選定版本**的原文。
 *
 * 刻意用 `baseline.text` 而非「該軌最新版」：使用者可能選的是某支舊草稿，回到最新版等於
 * 把他選的版本也一起換掉，那不叫「恢復」。
 */
function resetPrompt(): void {
  if (!defaults.value) return;
  systemPrompt.value = baseline.value.text;
}

function clearRun(): void {
  rawOutput.value = '';
  result.value = null;
  usage.value = null;
  meta.value = null;
  warnings.value = [];
  errorMessage.value = '';
}

async function run(): Promise<void> {
  if (!canRun.value || streaming.value) return;
  clearRun();
  streaming.value = true;
  abortController = new AbortController();
  try {
    await streamPromptDebug(
      {
        text: inputText.value,
        system_prompt: systemPrompt.value,
        overrides: llm.overrides.value,
      },
      {
        onMeta: (value) => (meta.value = value),
        onDelta: async (text) => {
          rawOutput.value += text;
          await nextTick();
          if (outputRef.value) outputRef.value.scrollTop = outputRef.value.scrollHeight;
        },
        onWarning: (message) => warnings.value.push(message),
        onResult: (value) => (result.value = value),
        onUsage: (value) => (usage.value = value),
        onError: (message) => (errorMessage.value = message),
      },
      abortController.signal,
    );
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      errorMessage.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    streaming.value = false;
    abortController = null;
  }
}

function abort(): void {
  abortController?.abort();
}

async function copyOutput(): Promise<void> {
  if (!rawOutput.value) return;
  await navigator.clipboard.writeText(rawOutput.value);
  Message.success('已複製 AI 輸出');
}

function openLlmSettings(): void {
  void router.replace({ query: { ...route.query, settings: 'llm' } });
}
</script>

<template>
  <!-- debug-page：三欄同排時滿高不整頁捲，捲動下沉到各區塊內部（窄屏於 style 內回退整頁捲） -->
  <div class="debug-page flex h-full min-h-full flex-col gap-4 overflow-hidden">
    <section class="shrink-0 rounded-xl border border-[#e5e6eb] bg-white px-5 py-4 shadow-sm">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div class="mb-1 flex items-center gap-2">
            <h1 class="text-lg font-semibold text-[#1d2129]">Prompt 調試台</h1>
            <a-tag color="arcoblue">售後根因分類</a-tag>
          </div>
          <p class="m-0 text-sm text-[#86909c]">
            任意貼入完整 IM session，使用可編輯 Prompt
            與臨時模型旋鈕，查看逐字串流、結構校驗與單次費用。
          </p>
        </div>
        <div v-if="defaults" class="flex flex-wrap gap-2 text-xs">
          <a-tag>{{ defaults.L2_count }} 個受控分類</a-tag>
          <a-tag>{{ defaults.analyzed_rows.toLocaleString() }} 筆裁判資料</a-tag>
          <a-tag color="orange">OOT {{ (defaults.oot_rate * 100).toFixed(1) }}%</a-tag>
          <a-tag color="green">平均信心 {{ defaults.mean_confidence.toFixed(3) }}</a-tag>
        </div>
      </div>
      <div v-if="defaults" class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#4e5969]">
        <span>依據：</span>
        <a
          :href="defaults.sources.knowledge_document.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.knowledge_document.title }}
        </a>
        <a
          :href="defaults.sources.judge_spreadsheet.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.judge_spreadsheet.title }}
        </a>
        <a
          :href="defaults.sources.field_definitions_document.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.field_definitions_document.title }}
        </a>
      </div>
    </section>

    <div class="debug-grid min-h-0 flex-1">
      <section class="debug-panel flex min-h-0 flex-col">
        <div class="panel-head shrink-0">
          <div>
            <div class="panel-title">System Prompt</div>
            <div class="panel-sub">
              已注入 {{ defaults?.L2_count ?? '—' }} 類操作定義；可直接改寫或整篇貼入做 A/B 調試
            </div>
          </div>
          <a-space size="mini">
            <a-button
              type="text"
              size="small"
              :disabled="loadingDefaults"
              @click="versionVisible = true"
              >版本列表</a-button
            >
            <a-button
              type="outline"
              status="warning"
              size="small"
              :disabled="loadingDefaults || streaming || savingVersion || !isEdited"
              @click="resetPrompt"
              >恢復此版本</a-button
            >
            <a-button
              type="primary"
              size="small"
              :loading="savingVersion"
              :disabled="loadingDefaults || streaming || !isEdited"
              @click="saveVersion"
              >存為新草稿</a-button
            >
          </a-space>
        </div>
        <div class="mb-3 flex shrink-0 flex-col gap-1">
          <!-- 多控制項橫排且可能換行 → 依前端規則用 a-row/a-col（gutter 同時管欄距與換行行距），
               不用裸 a-space：radio+select+button 三個變寬元件塞單一 flex 容器必然擠壓換行 -->
          <a-row :gutter="[8, 8]" align="center" wrap>
            <a-col flex="none">
              <!-- 本頁唯一口徑來源：編輯器載入 / 單次測試 / 跑批三者全部跟它走 -->
              <a-radio-group
                :model-value="track"
                type="button"
                size="small"
                :disabled="loadingDefaults || streaming || savingVersion"
                @change="(v) => setTrack(v as 'draft' | 'release')"
              >
                <a-radio value="draft">草稿</a-radio>
                <a-tooltip :content="hasRelease ? '' : '尚無正式版'" :disabled="hasRelease">
                  <a-radio value="release" :disabled="!hasRelease">正式</a-radio>
                </a-tooltip>
              </a-radio-group>
            </a-col>
            <a-col flex="200px">
              <!-- 選任一版即時載入其全文（不限最新版）。軌別已由左側 radio 表明，此處不重複 -->
              <a-select
                :model-value="baseline.name"
                class="w-full"
                size="small"
                :options="versionOptions"
                :loading="loadingVersion"
                :disabled="loadingDefaults || streaming || savingVersion"
                placeholder="—"
                @change="(v) => selectVersion(v as string)"
              />
            </a-col>
            <a-col flex="none">
              <a-tooltip :content="promoteBlockedReason" :disabled="!promoteBlockedReason">
                <a-button
                  type="outline"
                  size="small"
                  :disabled="!promotableDraft || loadingDefaults || streaming || savingVersion"
                  @click="openPromoteForCurrent"
                  >升為正式版</a-button
                >
              </a-tooltip>
            </a-col>
            <a-col flex="none">
              <a-tag v-if="isEdited" color="orange" size="small">已編輯（未存檔）</a-tag>
              <a-tag v-else-if="track === 'release'" color="green" size="small">線上口徑</a-tag>
            </a-col>
          </a-row>
          <span class="text-xs leading-relaxed text-[#86909c]">
            共 {{ defaults?.drafts.length ?? 0 }} 支草稿、{{
              defaults?.releases.length ?? 0
            }}
            個正式版。<br />
            這裡是<strong class="font-medium text-[#4e5969]">草稿工作台</strong
            >：載入即接續最新草稿，測試與跑批都跑當前口徑。<br />
            改完按「存為新草稿」留一版；驗證滿意後到「版本列表」對該草稿按「升為正式版」才影響線上
          </span>
        </div>
        <a-textarea
          v-model="systemPrompt"
          class="prompt-editor"
          :disabled="loadingDefaults || streaming"
          :auto-size="false"
          placeholder="載入 Prompt 中…"
        />
        <div class="panel-foot">
          {{ systemPrompt.length.toLocaleString() }} 字元 ·
          編輯後直接送出只影響本次；存草稿也不影響線上，要上線須經「設為正式版」
        </div>
      </section>

      <section class="debug-panel flex min-h-0 flex-col">
        <div class="panel-head shrink-0">
          <div>
            <div class="panel-title">調試文本</div>
            <div class="panel-sub">請貼完整對話；模型會把其中的指令視為資料而非系統命令</div>
          </div>
          <a-button size="small" :disabled="streaming" @click="inputText = ''">清空</a-button>
        </div>
        <div class="mb-3 flex shrink-0 flex-wrap gap-2">
          <a-button
            v-for="sample in samples"
            :key="sample.label"
            size="mini"
            :disabled="streaming"
            @click="inputText = sample.text"
          >
            {{ sample.label }}
          </a-button>
        </div>
        <a-textarea
          v-model="inputText"
          class="input-editor"
          :disabled="streaming"
          :auto-size="false"
          placeholder="例如：\n[USER] 我仍未收到電子票…\n[BOT] …\n[USER] 請幫我查詢"
        />
        <div class="mt-3 flex shrink-0 items-center justify-between gap-3">
          <span class="text-xs text-[#86909c]">{{ inputText.length.toLocaleString() }} 字元</span>
          <a-space>
            <a-badge :count="reviewCount" :max-count="99" :offset="[-4, 4]">
              <a-button
                type="outline"
                :disabled="streaming || !systemPrompt.trim()"
                @click="openRevise()"
              >
                案例庫／AI 改寫
              </a-button>
            </a-badge>
            <a-button
              type="outline"
              :disabled="streaming || !systemPrompt.trim()"
              @click="batchVisible = true"
            >
              跑批
            </a-button>
            <a-button v-if="streaming" status="danger" @click="abort">停止</a-button>
            <a-button v-else type="primary" size="large" :disabled="!canRun" @click="run">
              開始裁決
            </a-button>
          </a-space>
        </div>
      </section>

      <section class="flex min-h-0 flex-col gap-3">
        <div class="debug-panel flex-none">
          <div class="panel-head">
            <div>
              <div class="panel-title">模型配置</div>
              <div class="panel-sub">
                選一個具名配置；內容在「設定 › LLM 設定」統一維護，一筆可同時給多個功能區用
              </div>
            </div>
            <!-- shrink-0 + nowrap：panel-head 是 flex，左側說明文字變長時會把連結擠到換行 -->
            <a-link class="shrink-0 whitespace-nowrap" @click="openLlmSettings"
              >管理 LLM 設定</a-link
            >
          </div>
          <a-alert
            v-if="!Object.keys(llm.providerHasToken.value).length"
            type="warning"
            class="mb-3"
          >
            尚無可用 LLM 連線，請先至「設定 › LLM 設定」建立並保存 API Token。
          </a-alert>
          <LlmConfigSelect
            v-model="llm.configId.value"
            :configs="llm.configs.value"
            :provider-has-token="llm.providerHasToken.value"
            :show-manage-link="false"
          />
        </div>

        <div class="output-panel debug-panel flex min-h-0 flex-1 flex-col">
          <div class="panel-head flex-none">
            <div>
              <div class="flex items-center gap-2">
                <div class="panel-title">AI 流式輸出</div>
                <a-tag v-if="streaming" color="arcoblue" size="small">生成中</a-tag>
                <a-tag v-else-if="result?.valid" color="green" size="small">Schema 通過</a-tag>
                <a-tag v-else-if="result" color="red" size="small">需修 Prompt</a-tag>
              </div>
              <div class="panel-sub">原始 JSON 逐 token 顯示；完成後再做欄位相依校驗</div>
            </div>
            <a-button size="small" :disabled="!rawOutput" @click="copyOutput">複製</a-button>
          </div>

          <!-- 輸出區捲動容器：串流黑框吃剩餘高度，結果／費用接在其下一起於本容器內捲動 -->
          <div class="stream-scroll flex min-h-0 flex-1 flex-col">
            <a-alert v-if="errorMessage" type="error" class="mb-3 shrink-0">{{
              errorMessage
            }}</a-alert>
            <a-alert
              v-for="message in warnings"
              :key="message"
              type="warning"
              class="mb-2 shrink-0"
              >{{ message }}</a-alert
            >

            <pre ref="outputRef" class="stream-output">{{
              rawOutput || '尚未執行。開始裁決後，這裡會逐字顯示模型輸出。'
            }}</pre>

            <div v-if="result" class="mt-3 shrink-0">
              <a-alert v-if="result.validation_issues.length" type="error" class="mb-3">
                <div class="font-medium">輸出契約未通過</div>
                <div v-for="issue in result.validation_issues" :key="issue" class="mt-1 text-xs">
                  • {{ issue }}
                </div>
              </a-alert>
              <PromptReviewPanel
                v-if="displayedResults.length && result.parsed"
                :fields="displayedResults"
                :schema="defaults?.output_schema"
                :cascade="defaults?.output_cascade"
                :ai-output="result.parsed"
                :conversation="inputText"
                :prompt-version="isEdited ? '' : baseline.name"
                :model="meta?.model ?? llm.overrides.value.model"
                :disabled="streaming"
                @saved="onCaseSaved"
                @revise="(id: number) => openRevise(1, id)"
              />
            </div>

            <div v-if="usage" class="usage-card mt-3 shrink-0">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="text-xs text-[#86909c]">本次估算費用</div>
                  <div class="text-xl font-semibold text-[#1d2129]">
                    US$ {{ usage.cost_usd.toFixed(6) }}
                  </div>
                </div>
                <div class="text-right text-xs text-[#4e5969]">
                  <div>
                    {{ usage.total_tokens.toLocaleString() }} tokens ·
                    {{ (usage.latency_ms / 1000).toFixed(1) }}s
                  </div>
                  <div>
                    輸入 {{ usage.prompt_tokens.toLocaleString() }} / 輸出
                    {{ usage.completion_tokens.toLocaleString() }}
                  </div>
                  <div v-if="usage.cached_tokens || usage.reasoning_tokens">
                    快取 {{ usage.cached_tokens.toLocaleString() }} / 推理
                    {{ usage.reasoning_tokens.toLocaleString() }}
                  </div>
                </div>
              </div>
              <div class="mt-2 text-[11px] text-[#86909c]">
                依目前模型單價與 API usage 估算，最終金額以供應商帳單為準。
              </div>
            </div>
            <div v-if="meta" class="mt-2 shrink-0 text-[11px] text-[#86909c]">
              {{ meta.model }} · {{ meta.provider }} · reasoning={{ meta.reasoning_effort }} ·
              temperature={{ meta.temperature ?? 'default' }}
            </div>
          </div>
        </div>
      </section>
    </div>

    <PromptDebugBatchDrawer
      v-model:visible="batchVisible"
      :system-prompt="systemPrompt"
      :prompt-version="isEdited ? '' : baseline.name"
      :prompt-kind="isEdited ? '' : track"
      :prompt-edited="isEdited"
      :drafts="defaults?.drafts ?? []"
      :releases="defaults?.releases ?? []"
      :configs="llm.configs.value"
      :default-config-id="llm.config.value?.id ?? ''"
      :provider-has-token="llm.providerHasToken.value"
    />

    <PromptVersionDrawer
      v-model:visible="versionVisible"
      :drafts="defaults?.drafts ?? []"
      :releases="defaults?.releases ?? []"
      :active-release="defaults?.active_release ?? ''"
      :promote-target="promoteTarget"
      @promoted="() => loadDefaults(true)"
      @promote-target-consumed="promoteTarget = ''"
    />

    <PromptReviseDrawer
      v-model:visible="reviseVisible"
      :system-prompt="systemPrompt"
      :prompt-version="defaults?.active_release ?? ''"
      :release-prompt="defaults?.release_prompt ?? ''"
      :prompt-edited="isEdited"
      :drafts="defaults?.drafts ?? []"
      :releases="defaults?.releases ?? []"
      :initial-step="reviseStep"
      :preselect-id="revisePreselectId"
      @count="reviewCount = $event"
      @saved-version="() => loadDefaults(false)"
      @step-change="onReviseStep"
      @request-batch="onRequestBatch"
    />
  </div>
</template>

<style scoped>
.debug-grid {
  display: grid;
  grid-template-columns: minmax(300px, 0.92fr) minmax(300px, 0.92fr) minmax(380px, 1.16fr);
  gap: 16px;
  align-items: stretch;
}
.debug-panel {
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  background: #fff;
  padding: 16px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 3%);
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.panel-title {
  color: #1d2129;
  font-size: 14px;
  font-weight: 600;
}
.panel-sub,
.panel-foot {
  color: #86909c;
  font-size: 11px;
  line-height: 1.5;
}
.panel-foot {
  flex: none;
  margin-top: 8px;
}
/* 兩個編輯器吃各自面板的剩餘高度（textarea 本身即捲動容器）；min-height 0 才收得住 */
.prompt-editor,
.input-editor {
  flex: 1;
  width: 100%;
  min-height: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  line-height: 1.55;
}
.stream-output {
  flex: 1;
  min-height: 150px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid #27272a;
  border-radius: 8px;
  background: #18181b;
  color: #d4d4d8;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
}
.stream-scroll {
  overflow-y: auto;
}
.usage-card {
  border: 1px solid #bedaff;
  border-radius: 10px;
  background: #f2f7ff;
  padding: 12px;
}
/*
  ≤1380px 起三欄折成 2+1 兩列，一屏塞不下 → 整體回退為「頁面捲動」（AppShell 內容區本就 overflow-y-auto）：
  面板恢復固定高度，各自的內部捲動容器改為隨內容展開，避免出現「一小塊能捲、下方大片留白」。
*/
@media (max-width: 1380px) {
  .debug-page {
    height: auto;
    overflow: visible;
  }
  .debug-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .debug-grid > section:last-child {
    grid-column: 1 / -1;
  }
  .prompt-editor,
  .input-editor {
    flex: none;
    height: 560px;
  }
  .output-panel {
    min-height: 360px;
  }
  .stream-scroll {
    overflow: visible;
  }
  .stream-output {
    flex: none;
    height: 260px;
  }
}
@media (max-width: 880px) {
  .debug-grid {
    grid-template-columns: 1fr;
  }
  .debug-grid > section:last-child {
    grid-column: auto;
  }
}
</style>
