<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { IconDragDotVertical, IconPlus } from '@arco-design/web-vue/es/icon';
import { PERM } from '@/api';
import { AccordionGroup, LlmConfigTestResult, LlmKnobs } from '@/components';
import { areasUsingConfig, useListDragSort, useLlmConfigTest } from '@/composables';
import { usePermission } from '@/composables/usePermission';
import { useSettingsConfigsStore } from '@/stores';
import {
  LLM_AREA_DEFAULT_CONFIG_IDS,
  LLM_AREA_LABELS,
  PROVIDERS,
  defaultModelFor,
} from '../constants';
import { deriveConfigName, specKeyOf } from '../utils';
import type { LlmKnobs as Knobs, LlmModelConfig } from '../types';

// 單一供應商旗下的模型配置清單（手風琴，單開＝一次只編輯一筆）。
//
// **名稱＝規格**：沒有名稱輸入框，名字由 provider/model/旋鈕衍生（`deriveConfigName`），改參數
// 名字就跟著變。因此也沒有「複製」——想要變體就新增一筆再改一個旋鈕。
// 所有配置一視同仁可直接編輯，沒有出廠／自訂之分；唯一限制是「功能區預設起點不可刪」
// （與後端 `_validate_model_configs` 同一條規則，刪除鈕的 disabled 條件即由此而來）。
const props = defineProps<{ provider: string }>();

const store = useSettingsConfigsStore();
const activeKey = ref('');
/** 當前展開那筆的編輯草稿（含尚未落庫的新增筆）。 */
const draft = ref<LlmModelConfig | null>(null);
/** 新增中、尚未儲存的那筆（不在 store 裡，僅本地）。 */
const pending = ref<LlmModelConfig | null>(null);
const saving = ref(false);

/** 該供應商的配置（新增中的那筆也要出現在清單裡，否則按下新增什麼都沒發生）。 */
const configs = computed(() => {
  const saved = store.llmModelConfigs.filter((c) => c.provider === props.provider);
  return pending.value ? [...saved, pending.value] : saved;
});

// ── 拖動排序 ────────────────────────────────────────────────────────────────────────
const listWrapRef = ref<HTMLElement>();
/** SortableJS 的容器必須是拖曳項的**直接父層**＝Arco collapse 的根元素，故由外層包裹 div 查詢。 */
const collapseEl = computed(() => {
  void configs.value.length; // 清單長度變化時重查容器（DOM 查詢本身沒有響應式依賴）
  return listWrapRef.value?.querySelector<HTMLElement>('.arco-collapse') ?? null;
});

/**
 * 提交新順序。
 *
 * ⚠️ 手風琴只顯示**本供應商**的配置，但落庫的是整份跨供應商清單——若照 `persist()` 那樣
 * 「其他家全部排前面、本家排後面」，每拖一次就會把別家的相對位置整個洗掉。這裡改成「就地替換」：
 * 走過原始全域清單，遇到本供應商的槽位就依序填入重排後的結果，其餘位置原封不動。
 */
const commitOrder = async (nextForProvider: LlmModelConfig[]): Promise<void> => {
  if (pending.value) return; // 有未儲存的新增筆時不排序（它還不在庫裡，排了也存不進去）
  const queue = [...nextForProvider];
  const next = store.llmModelConfigs.map((c) =>
    c.provider === props.provider ? (queue.shift() ?? c) : c,
  );
  saving.value = true;
  try {
    await store.saveLlmModelConfigs(next);
  } catch (e) {
    Message.error((e as Error)?.message || '排序儲存失敗');
  } finally {
    saving.value = false;
  }
};

useListDragSort(collapseEl, () => configs.value, commitOrder, {
  handle: '.cfg-drag-handle',
  draggable: '.arco-collapse-item',
});

/** 該供應商的官方文件連結（label → URL）；資料源＝llm_model.json `providers[].docs`。 */
const docs = computed(
  () => PROVIDERS.find((p) => p.id === props.provider)?.docs ?? ({} as Record<string, string>),
);

/**
 * 刪掉這筆之後，哪些功能區的**預設起點**會跟著失效、改用哪一筆。
 *
 * `areaDefaults` 指的是「該區還沒選過配置時的起點」；被刪之後 `useLlmAreaConfig` 的三級回落會
 * 讓該區改用清單第一筆——是降級不是損壞（例如 AI 定點改寫會從旗艦模型掉到便宜模型），但使用者
 * 有權在按下刪除前知道。故不禁止刪除，改為在確認框把後果講明白。
 */
const areaDefaultImpact = (id: string): string => {
  const areas = Object.entries(LLM_AREA_DEFAULT_CONFIG_IDS)
    .filter(([, cfgId]) => cfgId === id)
    .map(([area]) => LLM_AREA_LABELS[area] ?? area);
  if (!areas.length) return '';
  const next = store.llmModelConfigs.find((c) => c.id !== id);
  return `${areas.join('、')} 的預設起點是這一筆，刪除後會改用「${next?.name ?? '（清單第一筆）'}」`;
};

/** 展開哪一筆就對哪一筆建草稿；收合即丟。新增中的那筆草稿保留（否則一收合就沒了）。 */
watch(
  [activeKey, configs],
  ([id]) => {
    if (pending.value && id === pending.value.id) {
      draft.value = draft.value?.id === id ? draft.value : { ...pending.value };
      return;
    }
    const hit = store.llmModelConfigs.find((c) => c.id === id);
    draft.value = hit ? { ...hit } : null;
  },
  { immediate: true },
);

/** 手風琴 header：展開中綁草稿（改旋鈕名字即時跟著變），未展開綁已存值。 */
const headerName = (c: LlmModelConfig): string =>
  draft.value && draft.value.id === c.id ? deriveConfigName(draft.value) : c.name;

/** 哪些功能區正用著這筆（綁定在 DB，故為跨使用者的真實引用，非只有自己這台）。 */
const usedBy = (id: string): string =>
  areasUsingConfig(store.llmAreaConfigs, id)
    .map((a) => LLM_AREA_LABELS[a] ?? a)
    .join('、');

/** 草稿與**其他**配置撞規格時的那一筆（撞了就不能存——規格相同即同一筆配置）。 */
const conflict = computed<LlmModelConfig | null>(() => {
  if (!draft.value) return null;
  const key = specKeyOf(draft.value);
  return (
    store.llmModelConfigs.find((c) => c.id !== draft.value!.id && specKeyOf(c) === key) ?? null
  );
});

/**
 * 送出整份**跨供應商**清單（後端是整包替換語義）。
 *
 * ⚠️ 關鍵：本元件只管自己這個供應商那幾筆，送出時必須把**其他供應商**的配置原樣帶上——
 * 漏帶就等於把別家的配置全刪了。這是整包替換語義下最容易踩的坑。
 */
const persist = async (nextForThisProvider: LlmModelConfig[]): Promise<void> => {
  const others = store.llmModelConfigs.filter((c) => c.provider !== props.provider);
  saving.value = true;
  try {
    await store.saveLlmModelConfigs([...others, ...nextForThisProvider]);
  } catch (e) {
    // 後端校驗訊息（規格重複、旋鈕值域外、刪到功能區起點…）是寫給使用者看的，原樣顯示
    Message.error((e as Error)?.message || '儲存模型配置失敗');
    throw e;
  } finally {
    saving.value = false;
  }
};

/** 新增：建一筆**本地草稿**（不落庫），預填當前展開那筆的旋鈕，讓「同設定只差一個檔位」最省事。 */
const addConfig = (): void => {
  const base = draft.value;
  const created: LlmModelConfig = {
    id: `cfg-${crypto.randomUUID()}`,
    name: '',
    provider: props.provider,
    model: base?.provider === props.provider ? base.model : defaultModelFor(props.provider),
    thinking: base?.provider === props.provider ? base.thinking : 'default',
    reasoning_effort: base?.provider === props.provider ? base.reasoning_effort : 'default',
    temperature: base?.provider === props.provider ? base.temperature : null,
  };
  pending.value = created;
  draft.value = { ...created };
  activeKey.value = created.id;
};

const save = async (): Promise<void> => {
  if (!draft.value || conflict.value) return;
  const saved = store.llmModelConfigs.filter((c) => c.provider === props.provider);
  const exists = saved.some((c) => c.id === draft.value!.id);
  const next = exists
    ? saved.map((c) => (c.id === draft.value!.id ? { ...draft.value! } : c))
    : [...saved, { ...draft.value! }];
  await persist(next);
  pending.value = null;
  Message.success('已儲存');
};

/** 放棄尚未儲存的新增筆。 */
const discard = (): void => {
  pending.value = null;
  draft.value = null;
  activeKey.value = '';
};

const removeConfig = (c: LlmModelConfig): void => {
  if (c.id === pending.value?.id) {
    discard();
    return;
  }
  const notes = [
    usedBy(c.id) && `${usedBy(c.id)} 正在使用它（全團隊共用），刪除後會自動回到出廠預設`,
    areaDefaultImpact(c.id),
  ].filter(Boolean);
  Modal.confirm({
    title: '刪除模型配置',
    content: notes.length
      ? `刪除「${c.name}」：${notes.join('；')}。確定要刪除？`
      : `確定要刪除「${c.name}」？`,
    okText: '刪除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      const saved = store.llmModelConfigs.filter((c2) => c2.provider === props.provider);
      await persist(saved.filter((x) => x.id !== c.id));
      Message.success('已刪除');
    },
  });
};

/** LlmKnobs 的 v-model：草稿裡的旋鈕欄位（id/provider 是配置身分，不歸旋鈕管）。 */
const draftKnobs = computed<Knobs>({
  get: () => ({
    model: draft.value?.model ?? '',
    temperature: draft.value?.temperature ?? null,
    thinking: draft.value?.thinking ?? 'default',
    reasoning_effort: draft.value?.reasoning_effort ?? 'default',
  }),
  set: (v) => {
    if (draft.value) Object.assign(draft.value, v);
  },
});

const { can } = usePermission();
/** 測試連線打的是真 LLM、用團隊共用 token，後端 `/api/settings/test-llm` 要 manage 權限，UI 同步收斂。 */
const canTest = computed(() => can(PERM.settingsLlmConfigManage));

/**
 * 測試連線：測的是**當前展開那筆的草稿值**（尚未儲存也能測——先驗證再存才是正確順序）。
 *
 * 手風琴單開，同時只有一筆草稿，故整個清單共用一個 composable 實例即可；切換配置時
 * `useLlmConfigTest` 內部的 watch 會偵測到旋鈕變動而清空上次結果，不會把 A 的結果留在 B 上。
 */
const llmTest = useLlmConfigTest(
  () => props.provider,
  () => draftKnobs.value,
);
</script>

<template>
  <div>
    <!-- 說明 + 新增 + 官方文件併成一個帶內距的淡底區塊：三者都是「這份清單怎麼用」的說明性內容，
         分成兩塊會各自貼邊、與下方卡片的左緣也對不齊。用淡填色而非邊框——下方手風琴每筆都是
         帶框卡片，這裡再加一圈框會搶視覺層級。
         官方文件連結是 provider 級資訊（`providers[].docs`），整個分頁渲染一次即可（原本掛在
         `LlmKnobs` 裡，於是該供應商每展開一筆配置就重印一次同一組連結）。 -->
    <div class="mb-4 rounded bg-[var(--color-fill-1)] px-4 py-3.5">
      <div class="flex items-start justify-between gap-4">
        <!-- ⚠️ 用 span 不用 p：本專案 Tailwind `preflight: false`（不做 reset，避免破壞 Arco），
             `<p>` 會保留瀏覽器預設的 `margin: 1em 0`，只清 mb-0 仍會被上邊距推低、與按鈕對不齊。 -->
        <span class="min-w-0 flex-1 text-[13px] leading-[1.8] text-[var(--color-text-3)]">
          名稱由參數自動組成；規格相同的配置只能有一筆。一筆可同時給多個功能區使用，改一次全部生效。
        </span>
        <a-button
          type="primary"
          size="small"
          class="shrink-0"
          :disabled="!!pending"
          @click="addConfig"
        >
          <template #icon><IconPlus /></template>
          新增模型配置
        </a-button>
      </div>

      <div
        v-if="Object.keys(docs).length"
        class="mt-3 border-t border-[var(--color-neutral-3)] pt-3"
      >
        <div class="mb-2 text-xs text-[var(--color-text-3)]">參數配置說明</div>
        <div class="flex flex-col gap-1.5 text-xs leading-[1.6]">
          <a
            v-for="(url, label) in docs"
            :key="url"
            :href="url"
            target="_blank"
            rel="noopener noreferrer"
            class="w-fit text-[rgb(var(--primary-6))] hover:underline"
            >{{ label }} ↗</a
          >
        </div>
      </div>
    </div>

    <a-empty v-if="!configs.length" description="這個供應商還沒有任何模型配置" />

    <div v-else ref="listWrapRef">
      <AccordionGroup v-model:active="activeKey">
        <a-collapse-item v-for="c in configs" :key="c.id" :header="headerName(c) || '（新配置）'">
          <template #extra>
            <a-space size="mini">
              <a-tag v-if="c.id === pending?.id" size="small" color="orange">未儲存</a-tag>
              <span v-else-if="usedBy(c.id)" class="text-xs text-[var(--color-text-3)]">
                用於：{{ usedBy(c.id) }}
              </span>
              <!-- @click.stop：把手在 header 內，不加會連帶觸發展開/收合。
                   有未儲存的新增筆時不給拖（它還不在庫裡，排了也存不進去）。 -->
              <span
                v-if="!pending"
                class="cfg-drag-handle cursor-grab px-1 text-[var(--color-text-3)] active:cursor-grabbing"
                title="拖動以調整順序"
                @click.stop
              >
                <IconDragDotVertical />
              </span>
            </a-space>
          </template>

          <template v-if="draft && draft.id === c.id">
            <LlmKnobs v-model="draftKnobs" :provider="provider" />

            <a-alert v-if="conflict" type="warning" class="mt-3">
              該配置已存在：「{{ conflict.name }}」——想調整就直接編輯那一筆；要另建一筆，
              請至少改動一個參數（model / 思考模式 / 推理檔位 / temperature）。
            </a-alert>

            <LlmConfigTestResult
              :result="llmTest.testResult.value"
              :provider="provider"
              :knobs="draftKnobs"
              class="mt-3"
            />

            <!-- 三顆動作鈕統一靠右下角，順序＝刪除 / 測試連線 / 儲存：破壞性動作在最左、離主行為最遠，
               主行為（儲存）在最右＝視線終點。測試連線用綠色（`primary status="success"`＝產出/驗證類），
               與連線卡的「測試連線」同色同語義。 -->
            <div class="mt-3 flex items-center justify-end gap-2">
              <a-button size="small" type="outline" status="danger" @click="removeConfig(c)">
                {{ c.id === pending?.id ? '放棄' : '刪除' }}
              </a-button>
              <a-button
                v-if="canTest"
                size="small"
                type="primary"
                status="success"
                :loading="llmTest.testing.value"
                :disabled="!draftKnobs.model"
                @click="llmTest.onTest"
              >
                測試連線
              </a-button>
              <a-button
                size="small"
                type="primary"
                :loading="saving"
                :disabled="!!conflict"
                @click="save"
              >
                儲存
              </a-button>
            </div>
          </template>
        </a-collapse-item>
      </AccordionGroup>
    </div>
  </div>
</template>
