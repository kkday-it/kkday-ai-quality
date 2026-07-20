# app/core/permissions — 可替換權限框架

破壞性端點的授權層。設計目標：**現行只要 admin/qc 兩級的效果，但資料形狀 / 命名 / 介面切面全部長得像 be2**，
日後串接 be2 中央 Auth SVC ＝換 provider + 換前端 `fetchPermissions`，不重寫 router。

## 結構
| 檔 | 職責 |
|---|---|
| `base.py` | `PermissionProvider` Protocol（`get_permissions(user)` / `check(user, perm)`）；fail-closed 為介面契約。 |
| `permission_keys.py` | business-key 具名常數（be2 風格 `module.sub-function.action`）+ `ALL_KEYS`。禁在 router 散寫字面字串。 |
| `local_provider.py` | `LocalPermissionProvider`：`auth.role_for(email)` 派生角色 → 讀 `config/global/role_permissions.json`（admin `'*'` 展全量）。 |
| `be2_provider.py` | `Be2PermissionProvider` **過渡實作**：get_permissions/check 委派 local role map（行為安全等價·fail-closed），支撐「登入先切 be2、授權後切」漸進路徑；正式 Auth SVC 契約接通後只改本檔內部（兩條路徑見檔頭）。 |
| `deps.py` | `require_permission(key)`（FastAPI 依賴工廠·fail-closed 403）+ `get_provider()`（讀 `auth.config.json['provider']`·唯一分流點）+ `business_list_ttl_ms()`。 |

## 用法（router）
```python
from app.core.permissions import require_permission, permission_keys

@router.post("/...")
def handler(user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE))):
    ...
```

## business-key ↔ 端點 ↔ 角色
| key | 端點 | 角色 |
|---|---|---|
| `judge-rule.version.manage` | judge-rules save/restore/reset×2 | admin |
| `data.datapack.import` | admin import validate/run | qc+admin（業務拍板：登入即可用全部資料導入功能） |
| `data.datapack.export` | admin export/start | qc+admin |
| `data.source.upload` | inbound validate/upload | qc+admin |
| `finding.review.update` | findings PATCH /status | qc+admin |
| `problem.list.export` | problems POST /export | qc+admin |

角色→key 映射 SSOT＝`config/global/role_permissions.json`；provider 切換＝`config/global/auth.config.json`。

## 換 be2（漸進路徑·唯一改動點）

**登入與授權兩鍵獨立**（auth.config.json：`authProvider`=登入／`provider`=授權），可分開切換：

1. **先切登入**：`authProvider='be2'`（core/auth_verifiers.py Be2TokenVerifier——email 自動
   provision；驗簽契約接通前 production 啟用即拒）；授權續用 local map 或 be2 過渡委派，行為不變。
2. **後切授權**：auth team business-list 契約到位後，改 `be2_provider.py` 內部（登入 response
   businessList 透傳 or 每請求 verify，見該檔檔頭）＋ `provider='be2'`。
3. 前端 `api/permission.api.ts::fetchPermissions` 改讀 be2 SDK/localStorage（快取 wrapper
   已對齊 be2 `{value, ttl, startTime}` 形狀，同網域可互通）。

router / 前端 store / directive / router 守衛 / 選單全不動。
