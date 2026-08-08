import { describe, expect, it, vi } from 'vitest';

const arco = vi.hoisted(() => ({ Modal: { info: vi.fn() } }));
vi.mock('@arco-design/web-vue', () => arco);

const { notifyComingSoon } = await import('./comingSoon.util');

describe('notifyComingSoon', () => {
  it('用訊息型 Modal（不違反 Drawer-first 對內容型彈出層的禁令）', () => {
    notifyComingSoon('判決歸因');
    expect(arco.Modal.info).toHaveBeenCalledTimes(1);
    const [cfg] = arco.Modal.info.mock.lastCall ?? [];
    expect(cfg?.hideCancel).toBe(true);
    expect(cfg?.content).toContain('判決歸因');
  });

  it('文案不使用刪節號（對齊動作標籤規則）', () => {
    notifyComingSoon('判決歸因');
    const [cfg] = arco.Modal.info.mock.lastCall ?? [];
    expect(cfg?.title).toBe('即將推出');
    expect(`${cfg?.title}${cfg?.content}`).not.toMatch(/[…]|\.\.\./);
  });

  it('可帶補充說明，優先於預設文案', () => {
    notifyComingSoon('判決歸因', '目前可先用「人工糾正」調整 AI 的分類');
    const [cfg] = arco.Modal.info.mock.lastCall ?? [];
    expect(cfg?.content).toBe('目前可先用「人工糾正」調整 AI 的分類');
  });
});
