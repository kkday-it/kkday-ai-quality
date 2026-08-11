<script setup lang="ts">
/**
 * 備註互動類型（note_type）值域維護。
 *
 * 值域是業務會調的參照資料，但 `item_code` 會被既有備註引用——所以**沒有刪除**：停用一律走
 * `is_active=false`（從可選清單消失，歷史備註仍解析得到 label）。改 code 等於改歷史語義，故 code
 * 只在新增時可填、既有項一律唯讀。
 *
 * 底層 `attribution_dimension_master` 以 `dimension_code` 判別、保留多軸能力，目前只有這一軸
 * （責任方／嚴重度／建議行動三軸因零消費者已於 f2a91c7b4d08 清退）。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconEdit, IconPlus } from '@arco-design/web-vue/es/icon';
import { PERM, type DimensionItem, getDimensions, saveDimensionItem } from '@/api';
import { AsyncSection, TableLayout } from '@/components';
import { usePermission } from '@/composables/usePermission';

const props = defineProps<{ active?: boolean }>();

const { can } = usePermission();
const canManage = computed(() => can(PERM.attributionDimensionManage));

const AXIS = 'note_type';
const data = ref<Record<string, DimensionItem[]>>({});
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const draft = ref<DimensionItem | null>(null);

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    // 維護畫面要看得到停用項，否則使用者會以為資料不見了
    data.value = await getDimensions(true);
  } catch (e: unknown) {
    error.value = (e as Error)?.message || '載入值域失敗';
  } finally {
    loading.value = false;
  }
};

watch(() => props.active, (v) => v && !Object.keys(data.value).length && void load(), {
  immediate: true,
});

// TableLayout 的 data 是 Record<string, unknown>[]；DimensionItem 沒有 index signature，
// 這裡做一次淺層轉型（欄位皆為原生型別，無執行期成本）。
const rows = computed(
  () => (data.value[AXIS] ?? []) as unknown as Record<string, unknown>[],
);

const startEdit = (row?: DimensionItem) => {
  draft.value = row
    ? { ...row }
    : { dimension_code: AXIS, item_code: '', item_label: '', sort_order: rows.value.length };
};

const save = async () => {
  if (!draft.value) return;
  saving.value = true;
  try {
    await saveDimensionItem(draft.value);
    Message.success('已儲存');
    draft.value = null;
    await load();
  } catch (e: unknown) {
    Message.error((e as Error)?.message || '儲存失敗');
  } finally {
    saving.value = false;
  }
};

const toggleActive = async (row: DimensionItem) => {
  saving.value = true;
  try {
    await saveDimensionItem({ ...row, is_active: !row.is_active });
    await load();
  } catch (e: unknown) {
    Message.error((e as Error)?.message || '操作失敗');
  } finally {
    saving.value = false;
  }
};

// 欄寬是**比例權重**不是像素（見 .claude/rules/frontend-vue.md）：總和 < 容器時 Arco 等比拉伸，
// 總和 > 容器就出現橫向捲軸（窄容器表格禁止橫捲）。說明欄原本沒宣告 width，於是只分到
// 「容器 − 其餘欄總和」約 60px，表頭被裁成一個字。
// 總和刻意壓到 488——抽屜 640 扣掉 drawer body padding 與 StickyTabs 內距後，實際可用比想像窄，
// 先前 580 仍有橫捲。壓低不吃虧：比例不變，多的寬度會等比分回各欄。
// 說明欄拿最大權重（內容欄多拿到的寬度是有用的）；操作欄容器有 flex-wrap，窄了只會堆疊不會裁切。
const columns = [
  { title: '機器碼', dataIndex: 'item_code', width: 100 },
  { title: '顯示名', dataIndex: 'item_label', width: 88 },
  { title: '說明', dataIndex: 'item_desc', ellipsis: true, tooltip: true, width: 180 },
  { title: '狀態', slotName: 'status', width: 52 },
  { title: '操作', slotName: 'ops', width: 68 },
];
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2">
    <a-alert v-if="!canManage" type="info">
      你沒有維護備註類型的權限（唯讀）。改動值域會影響全庫既有備註的顯示，請聯繫有
      <code>attribution.dimension.manage</code> 權限的同事。
    </a-alert>

    <a-row :gutter="[8, 8]" align="center" wrap>
      <a-col flex="auto" />
      <a-col flex="none">
        <a-button
          type="primary"
          size="small"
          :disabled="!canManage"
          @click="startEdit()"
        >
          <template #icon><icon-plus /></template>
          新增選項
        </a-button>
      </a-col>
    </a-row>

    <AsyncSection :loading="loading" :error="error">
      <div class="min-h-0 flex-1 overflow-hidden">
        <TableLayout
          full-height
          :data="rows"
          :columns="columns"
          :pagination="false"
          row-key="item_code"
        >
          <template #status="{ record }">
            <a-tag size="small" :color="record.is_active ? 'green' : 'gray'">
              {{ record.is_active ? '啟用' : '停用' }}
            </a-tag>
          </template>
          <template #ops="{ record }">
            <div class="flex flex-wrap gap-1">
              <a-button type="text" size="mini" :disabled="!canManage" @click="startEdit(record)">
                <template #icon><icon-edit /></template>
                編輯
              </a-button>
              <a-button
                type="text"
                size="mini"
                :disabled="!canManage || saving"
                @click="toggleActive(record)"
              >
                {{ record.is_active ? '停用' : '啟用' }}
              </a-button>
            </div>
          </template>
        </TableLayout>
      </div>
    </AsyncSection>

    <!-- 編輯抽屜（巢狀於設定抽屜內，與 BdTagVertical 的既有模式一致） -->
    <a-drawer
      :visible="!!draft"
      :width="480"
      :ok-loading="saving"
      ok-text="儲存"
      cancel-text="取消"
      unmount-on-close
      @ok="save"
      @cancel="draft = null"
      @update:visible="(v: boolean) => !v && (draft = null)"
    >
      <template #title>{{ draft?.attribution_dimension_oid ? '編輯選項' : '新增選項' }}</template>
      <a-form v-if="draft" :model="draft" layout="vertical" size="small">
        <a-form-item label="機器碼">
          <a-input
            v-model="draft.item_code"
            :disabled="!!draft.attribution_dimension_oid"
            placeholder="如 supplier / P1 / escalate_ops"
          />
          <template #extra>
            <span class="text-xs text-[#86909c]">
              落庫後不可修改——歷史判決引用的就是這個值，改了等於改變歷史語義。
            </span>
          </template>
        </a-form-item>
        <a-form-item label="顯示名">
          <a-input v-model="draft.item_label" placeholder="如 供應商" />
        </a-form-item>
        <a-form-item label="判準說明">
          <a-textarea
            :model-value="draft.item_desc ?? ''"
            :auto-size="{ minRows: 2, maxRows: 4 }"
            placeholder="給定責的人看的口徑描述：什麼情況該選這一項"
            @update:model-value="(v: string) => draft && (draft.item_desc = v)"
          />
        </a-form-item>
        <a-form-item label="排序">
          <a-input-number v-model="draft.sort_order" :min="0" class="w-32" />
        </a-form-item>
      </a-form>
    </a-drawer>
  </div>
</template>
