# AI Recommendation Diagnostics
## 小組共同設計核心

### 1. 產品目標

我們的客戶是品牌／電商，例如「背包公司 A」。

客戶的核心目標是：

> **提高 ChatGPT、Gemini、Perplexity 等 AI 在消費者詢問購買建議時，推薦 A 品牌產品的機率。**

我們的核心假設是：

> **AI 沒有推薦某項產品，不一定代表產品真的比較差；也可能只是 AI 沒有取得足夠、清楚或容易搜尋到的產品資訊。**

因此我們真正想回答的問題是：

> **AI 沒推薦你，是因為產品輸了，還是資訊輸了？**

---

### 2. 核心產品定位

我們不是單純做：

- AI 廣告投放
- GEO / SEO for ChatGPT
- 品牌 mention tracking
- synthetic persona 市場調查

我們的核心是：

> **AI Recommendation Failure Diagnosis**

也就是分析：

> **Why did AI choose my competitor, and is that reason actually justified?**

產品的核心價值，是把 recommendation failure 拆成兩類：

#### Product Gap

AI 沒推薦品牌 A，是因為產品本身真的不符合該使用者需求。

例如：

- Osprey 有更好的背負系統
- A 沒有 hip belt
- A 的重量真的比較重
- A 的售價超過使用者預算

這類問題應該轉成：

- 長期產品定位
- 產品 roadmap
- feature priority
- target customer strategy

#### Information Gap

產品其實具有相關優勢，但 AI 沒有正確取得或理解。

例如：

- 官網沒有明確寫出某項 feature
- 重要規格藏得太深
- product description 太模糊
- structured data 不完整
- AI 搜尋時不容易找到相關頁面
- 第三方 review 缺乏 supporting evidence

這類問題應該轉成：

- 網頁內容更新
- FAQ
- product metadata / schema
- wording
- information architecture
- AI-search discoverability

---

### 3. 使用情境：背包公司 A

假設客戶是背包公司 A。

競品可能包含：

- Osprey
- CabinZero
- Decathlon
- Patagonia
- Cotopaxi

我們要分析不同消費者情境下，AI 最後推薦哪個品牌，以及原因。

---

### 4. Step 1 — 產生不同 Consumer Contexts

讓 AI 扮演大量不同角色，例如：

- 32 歲女性會計上班族，兩個月後去歐洲自由行
- 13 歲私立國中生，下週校外教學
- 33 歲健身愛好者，每天帶運動用品上下班
- EDA 工程師，需要攜帶 laptop、charger、文件
- 預算有限的大學生
- 常搭廉航的旅客
- 攝影愛好者
- hiking 使用者

每個 persona 先根據自己的背景形成需求，再去找產品。

例如：

> 32 歲上班族，要去歐洲自由行

可能形成：

- 30–35L
- lightweight
- comfortable
- laptop compartment
- low-cost airline compatible
- under €150

接著讓 AI：

1. 形成需求
2. 搜尋產品
3. 建立候選清單
4. 比較產品
5. 排名
6. 解釋推薦理由

### Persona 的定位

我們不主張：

> 100 個 AI persona = 100 個真人。

而是：

> **Persona is a test vector, not a real customer.**

我們使用不同 persona / context，目的是探索大量 plausible purchase intents，測試 AI recommendation system 在不同條件下的行為。

---

### 5. Step 2 — Recommendation Diagnosis

把所有 AI agents 的結果聚合。

我們需要分析：

- A 有多少次被找到
- A 有多少次進入候選清單
- A 有多少次最後被推薦
- A 在哪些情境贏
- A 在哪些情境輸
- AI 認為 A 的優點
- AI 認為 A 的缺點
- 哪些 competitor 在哪些 attributes 上勝出

例如：

A 在以下需求表現較好：

- budget
- lightweight
- low-cost airline
- simple design

A 在以下需求表現較差：

- long-duration carrying
- comfort
- back support
- hiking

接著最重要的是：

> **這些負面理由是真的產品缺點，還是 AI 的資訊不足？**

---

### 6. Step 3 — Evidence Audit

對每個 recommendation failure 做進一步檢查。

例如 AI 認為：

> A 的背負舒適度不如 Osprey。

系統要進一步檢查：

- A 官網是否其實有 padded back panel
- 是否有相關 specification
- 是否有 FAQ
- 是否有第三方 review
- AI 搜尋是否能找到這些資訊
- competitor 是否有更完整 evidence

最後輸出：

> **Product Gap**

或：

> **Potential Information Gap**

---

### 7. Step 4 — 給品牌 Actionable Recommendations

#### 短期：Information Optimization

如果問題是 information gap：

- 改 product page
- 增加明確產品 attributes
- 補 FAQ
- 改寫 product description
- 補 structured metadata
- 改善相關頁面的搜尋可見度
- 增加 specific use-case content

#### 長期：Product Strategy

如果問題是 product gap：

- 哪些 customer segments 不值得搶
- 哪些 segments 是產品真正優勢
- 下一代產品要補什麼 feature
- marketing positioning 應該怎麼調整

---

### 8. MVP

Hackathon MVP 不需要做完整的廣告平台。

最小流程：

1. 輸入品牌與競品
2. 自動建立 20–50 個 consumer contexts
3. 每個 context 產生購買需求
4. AI 搜尋並推薦產品
5. 聚合 recommendation results
6. 找出品牌經常輸的原因
7. 檢查品牌網站 evidence
8. 判斷 Product Gap vs Information Gap
9. 給出改善建議

---

### 9. Demo 核心畫面

假設：

**Brand:** CabinZero  
**Competitor:** Osprey  
**Category:** Travel backpack

結果：

- CabinZero Recommendation Share: 31%
- Osprey Recommendation Share: 58%

CabinZero 勝出的情境：

- budget traveler
- low-cost airline
- lightweight travel

CabinZero 輸掉的情境：

- long walking
- comfort
- hiking
- back support

使用者點：

> **Why are we losing?**

系統顯示：

> 41% of lost recommendations mention comfort or back support.

接著 audit CabinZero website：

> Relevant comfort features exist, but supporting information is weak or difficult for AI to retrieve.

最後：

> **Potential Information Gap Detected**

並產生網站修改建議。

---

### 10. 我們與既有 GEO 工具的差異

一般 GEO / LLM visibility tool 主要回答：

> 我的品牌有沒有被 ChatGPT 提到？

我們要回答：

> **為什麼 AI 最後選 competitor，而不是我？**

再進一步：

> **這個理由是真的產品差異，還是資訊落差？**

因此核心不是 visibility，而是：

> **Recommendation Failure Diagnosis**

---

### 11. 核心設計原則

1. 不宣稱能看到 AI hidden CoT。
2. 只分析 observable behavior：搜尋、來源、候選產品、ranking、推薦理由。
3. Persona 是 sampling mechanism，不是真人替代品。
4. 不保證修改網站後一定提高推薦率。
5. 產品價值在於找出 avoidable information disadvantages。
6. 最重要的 differentiation 是 Product Gap vs Information Gap。

---

### 12. 一句話定位

> **We help brands understand why AI recommends their competitors — and whether they’re losing the product battle or just the information battle.**

首頁核心問題：

> **Are you losing to a better product — or just better information?**

---

### 13. 長期 Vision

短期：

> AI recommendation diagnostics + information optimization

中期：

> AI-native customer research + product intelligence

長期：

> AI-native advertising / recommendation infrastructure

最終希望建立：

> **Intent × Product × Evidence × Recommendation**

的 dataset，幫助品牌理解 AI 如何形成購買決策，以及如何更有效地服務未來由 AI agents 主導的購物流程。
