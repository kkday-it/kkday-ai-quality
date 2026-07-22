# app/core/permissions — 可替換權限框架

破壞性端點的授權層。設計目標：**無角色、直接授予**（email → business-key 集合），資料形狀 / 命名 / 介面切面全部長得像 be2
——be2 直接下發每 user 一組 permission 字串（無角色中間層），日後串接 be2 中央 Auth SVC ＝換 provider + 換前端
`fetchPermissions`，不重寫 router。

## 結構
| 檔 | 職責 |
|---|---|
| `base.py` | `PermissionProvider` Protocol（`get_permissions(user)` / `check(user, perm)`）；fail-closed 為介面契約。 |
| `permission_keys.py` | business-key 具名常數（be2 風格 `module.sub-function.action`）+ `ALL_KEYS`。禁在 router 散寫字面字串。 |
| `local_provider.py` | `LocalPermissionProvider`：email 直接對照 `config/global/permissions.json` 的 `default ∪ grants[email]`（`no_auth_grant_all=true` 時無條件全通過；`grants[email]` 含 `'*'` 展全量）。 |
| `be2_provider.py` | `Be2PermissionProvider` **過渡實作**：get_permissions/check 委派 `LocalPermissionProvider`（行為安全等價·fail-closed），支撐「登入先切 be2、授權後切」漸進路徑；正式 Auth SVC 契約接通後只改本檔內部（兩條路徑見檔頭）。 |
| `deps.py` | `require_permission(key)`（FastAPI 依賴工廠·fail-closed 403）+ `get_provider()`（讀 `auth.config.json['provider']`·唯一分流點）+ `business_list_ttl_ms()`。 |

## 用法（router）
```python
from app.core.permissions import require_permission, permission_keys

@router.post("/...")
def handler(user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE))):
    ...
```

多種敏感度共用同一端點（如 `POST /api/settings` 同時涵蓋日常操作與敏感連線變更）時，不套 `require_permission`
（會整包卡死），改在 handler 內用 `get_provider().get_permissions(user)` 做欄位級判斷——見
`app/api/routers/settings.py::_check_patch_permissions` 範例。

## business-key ↔ 端點 ↔ default/grants
| key | 端點 | 授予層級 |
|---|---|---|
| `judge-rule.version.manage` | judge-rules save/restore/reset×2 | 僅 grants |
| `data.datapack.import` | admin import validate/run | default（登入即可用） |
| `data.datapack.export` | admin export/start | default |
| `data.source.upload` | inbound validate/upload | default |
| `finding.review.update` | findings PATCH /status | default |
| `problem.list.export` | problems POST /export | default |
| `prejudge.run` | v1/prejudge 啟動/暫停/恢復/停止 | default |
| `settings.llm-area-default.write` | POST /api/settings（僅 `llm_area_defaults` 欄位） | default |
| `settings.llm-config.manage` | POST /api/settings（`llm_connections`/`llm_tokens`/`provider_models` 欄位）、POST /api/settings/test-llm | 僅 grants |
| `settings.qc-config.manage` | POST /api/settings（`qc_connections`/`qc_passwords` 欄位）、POST /api/datasource/qc-db/test | 僅 grants |
| `settings.secret.read` | GET /api/settings/raw | 僅 grants |

email→key 映射 SSOT＝`config/global/permissions.json`（`default` + `grants`）；provider 切換＝`config/global/auth.config.json`。

## 換 be2（漸進路徑·唯一改動點）

**登入與授權兩鍵獨立**（auth.config.json：`authProvider`=登入／`provider`=授權），可分開切換：

1. **先切登入**：`authProvider='be2'`（core/auth_verifiers.py Be2TokenVerifier——email 自動
   provision；驗簽契約接通前 production 啟用即拒）；授權續用 local（`permissions.json`）或 be2 過渡委派，行為不變。
2. **後切授權**：auth team business-list 契約到位後，改 `be2_provider.py` 內部（登入 response
   businessList 透傳 or 每請求 verify，見該檔檔頭）＋ `provider='be2'`，同時把 `permissions.json`
   的 `no_auth_grant_all` 改 `false`（否則 be2 身分驗證形同虛設，人人仍全權）。
3. 前端 `api/permission.api.ts::fetchPermissions` 改讀 be2 SDK/localStorage（快取 wrapper
   已對齊 be2 `{value, ttl}` 形狀，同網域可互通）。

router / 前端 store / directive / router 守衛 / 選單全不動。
