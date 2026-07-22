// @vitest-environment jsdom
/**
 * StateGuard 三態守衛元件渲染測試（error > loading > empty > slot 優先序）。
 * 檔級 @vitest-environment jsdom：全域 test env 維持 node（純函式測試快），元件測試逐檔標註。
 */
import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import StateGuard from './StateGuard.vue';

const stubs = {
  // Arco 元件以 stub 呈現（不拉整套 Arco；只驗 StateGuard 自身的分支邏輯）
  'a-alert': { template: '<div class="stub-alert"><slot /></div>' },
  'a-spin': { template: '<div class="stub-spin" />' },
  'a-empty': {
    props: ['description'],
    template: '<div class="stub-empty">{{ description }}</div>',
  },
};

const factory = (props: Record<string, unknown>) =>
  mount(StateGuard, { props, slots: { default: '<p class="content">OK</p>' }, global: { stubs } });

describe('StateGuard', () => {
  it('error 優先級最高（同時 loading 也只顯 error）', () => {
    const w = factory({ error: '爆了', loading: true });
    expect(w.find('.stub-alert').text()).toContain('爆了');
    expect(w.find('.stub-spin').exists()).toBe(false);
    expect(w.find('.content').exists()).toBe(false);
  });

  it('loading 顯 spin、不渲染 slot', () => {
    const w = factory({ loading: true });
    expect(w.find('.stub-spin').exists()).toBe(true);
    expect(w.find('.content').exists()).toBe(false);
  });

  it('empty 顯空狀態（自訂文案）', () => {
    const w = factory({ empty: true, emptyText: '沒東西' });
    expect(w.find('.stub-empty').text()).toBe('沒東西');
  });

  it('無任何狀態 → 渲染 slot 內容', () => {
    const w = factory({});
    expect(w.find('.content').text()).toBe('OK');
  });
});
