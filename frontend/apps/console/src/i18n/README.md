# src/i18n — 可替換 i18n 挖字框架

UI 文案外部化框架。設計目標同權限框架：**現只 zh-TW 單語系，但翻譯來源可一鍵替換**（日後接 TMS）。

## 結構

| 檔                        | 職責                                                                                                                                                                                                                         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `loader.ts`               | **唯一可替換接縫** `loadLocaleMessages(locale)`：預設 Vite `import.meta.glob` 靜態讀 `locales/<locale>/*.json`；**be2 挖字分支已備**——auth.config.json `be2.langPlatform` 註冊後自動改走 `GET {apiLangUrl}/api/v1/uiLangList?lang[]=&platform=`（Bearer·攤平 map 直餵 vue-i18n·失敗降級靜態）；**Klingon 模式** `localStorage['locale.klingon-active']='1'` 譯文前置 `(key) ` 供 QA 核對挖字。i18n instance / `$t` / 元件全不動。 |
| `index.ts`                | vue-i18n（`legacy:false` Composition API + `globalInjection`）instance + `setupI18n(locale)`（mount 前 `.then` 載入）。                                                                                                      |
| `apiError.util.ts`        | 後端錯誤 code → i18n 翻譯橋接：`errorCodeToI18nKey(code)`（唯一轉換點·`errors.<CODE>`）+ `translateApiError(err)`（有 code 且有對映則翻譯，否則回退後端 message）。                                                          |
| `locales/<locale>/*.json` | 分 namespace 訊息（common / auth / errors / …）；key＝`<namespace>.<page>.<語意>`。                                                                                                                                          |

## 用法

```vue
<script setup lang="ts">
import { useI18n } from 'vue-i18n';
const { t } = useI18n();
</script>
<template>{{ t('auth.login.submitLogin') }}</template>
```

錯誤翻譯：`Message.error(translateApiError(e))`（後端 `raise_api_error(code, message)` → 前端據 code 對映）。

## i18n vs config-label 界線（禁重疊）

| 文案性質                                                     | 去處                                                                |
| ------------------------------------------------------------ | ------------------------------------------------------------------- |
| UI 靜態文案（按鈕 / 標題 / 提示）                            | **i18n**（`$t`）                                                    |
| 判準領域 label（tier / polarity / stage·DB 存 code·QC 可調） | **留** `config/ai_judge/prejudge.json`＋`verdict.json` / `constants/labels`（不挖） |
| debug / 內部代碼                                             | 留碼內                                                              |

判準：**後端也共用 / 存 DB？** 是→config/constants；否（純前端 UI）→i18n。

## 挖字策略（漸進）

- 逐 feature 試點挖字（已完成 pilot：`auth` login 頁 + `AUTH.*` error code）；其餘 feature touch-when-edit 逐步遷移，各自獨立可 revert。
- i18n key 缺項 → **禁自創文案**（見全域規則）；placeholder key 標記 + 上報翻譯。

## 換翻譯來源（日後·唯一改動點）

be2 分支已內建（見上表 loader.ts 列）；正式接通僅需 auth.config.json `be2.langPlatform` 填實值。
