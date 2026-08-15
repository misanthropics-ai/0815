import { useState } from 'react'
import { diagnosis, product } from './mock'

type Page = 'intake' | 'diagnosis'

const label = (value: string) => value.replaceAll('_', ' ')
const percentage = (value: number) => `${Math.round(value * 100)}%`

function App() {
  const [page, setPage] = useState<Page>('intake')
  const [inputMode, setInputMode] = useState<'url' | 'text'>('url')
  const [input, setInput] = useState(product.sourceUrl)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [showEvidence, setShowEvidence] = useState(false)

  const analyze = () => {
    if (!input.trim()) return
    setIsAnalyzing(true)
    window.setTimeout(() => { setIsAnalyzing(false); setPage('diagnosis') }, 850)
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(event) => { event.preventDefault(); setPage('intake') }}>
          <span className="brand-mark">S</span><span>Signal Audit</span>
        </a>
        <p className="workspace">WORKSPACE</p>
        <nav>
          <button className={page === 'intake' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('intake')}><span>＋</span> Add product</button>
          <button className={page === 'diagnosis' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('diagnosis')}><span>◈</span> Diagnosis</button>
          <button className="nav-item" disabled><span>◌</span> Debate <em>soon</em></button>
        </nav>
        <div className="sidebar-foot"><span className="dot" /> Mock mode · Contract v2</div>
      </aside>

      <section className="content">
        {page === 'intake' ? (
          <>
            <header className="page-heading"><div><p className="eyebrow">PRODUCT EVIDENCE AUDIT</p><h1>Find what AI cannot see.</h1><p>Inspect the signals your product page gives an AI before it recommends a competitor.</p></div><span className="stage">01 · INTAKE</span></header>
            <section className="intake-card">
              <div className="toggle" aria-label="Input type"><button className={inputMode === 'url' ? 'selected' : ''} onClick={() => setInputMode('url')}>Product URL</button><button className={inputMode === 'text' ? 'selected' : ''} onClick={() => setInputMode('text')}>Product description</button></div>
              <label htmlFor="product-input">{inputMode === 'url' ? 'Link to a public product page' : 'Paste your product description'}</label>
              {inputMode === 'url' ? <input id="product-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder="https://…" /> : <textarea id="product-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Describe the product, its materials, specs and use cases…" />}
              <div className="intake-actions"><p>We extract only evidence stated on the page. Missing claims remain unknown.</p><button className="primary" disabled={isAnalyzing || !input.trim()} onClick={analyze}>{isAnalyzing ? 'Extracting evidence…' : 'Analyze product →'}</button></div>
            </section>
            <section className="preview"><div><p className="eyebrow">DEMO DATASET</p><h2>CabinZero Classic 36L</h2><p>Use the contract fixture to preview the completed audit.</p></div><button className="secondary" onClick={() => setPage('diagnosis')}>Open sample diagnosis</button></section>
          </>
        ) : (
          <>
            <header className="product-header"><div><button className="back" onClick={() => setPage('intake')}>← Products</button><p className="eyebrow">DIAGNOSIS · {product.productRef}</p><h1>{product.displayName}</h1><a href={product.sourceUrl} target="_blank" rel="noreferrer">{product.sourceUrl.replace('https://', '')} ↗</a></div><span className="stage">02 · DIAGNOSIS</span></header>
            <section className="score-grid">
              <article className="score-card main-score"><p>Recommendation share</p><strong>{percentage(diagnosis.recommendationShare)}</strong><span>across {diagnosis.simulations} simulated decisions</span></article>
              <article className="score-card"><p>Competitive gap</p><div className="versus"><span>CZ <b>{percentage(diagnosis.recommendationShare)}</b></span><i>vs</i><span>Osprey <b>{percentage(diagnosis.competitor.share)}</b></span></div><div className="bar"><span style={{ width: percentage(diagnosis.recommendationShare) }} /></div></article>
              <article className="score-card"><p>Strongest signals</p>{diagnosis.winners.map((item) => <div className="signal" key={item.name}><span>{item.name}</span><b>{percentage(item.share)}</b></div>)}</article>
            </section>
            <section className="section-title"><div><p className="eyebrow">EVIDENCE GAPS</p><h2>Why recommendations are being lost</h2><p>Ranked by impact on your recommendation share.</p></div><span className="count">{diagnosis.defects.length} findings</span></section>
            <section className="defect-list">
              {diagnosis.defects.map((defect) => <article className="defect-card" key={defect.attribute}><div className="defect-top"><span className={`severity ${defect.severity}`}>{defect.severity}</span><span className="attribute">{label(defect.attribute)}</span><span className="loss">{percentage(defect.loss)} lost in cluster</span></div><h3>{defect.headline}</h3><div className="defect-columns"><blockquote>{defect.reason}</blockquote><p><b>Competitor evidence</b>{defect.contrast}</p></div><footer><p><b>Suggested fix</b>{defect.fix}</p><button className="discuss" onClick={() => window.alert('Debate view is the next P5 milestone.')}>Discuss this →</button></footer></article>)}
            </section>
            <section className="attributes"><div className="attribute-heading"><div><p className="eyebrow">EXTRACTED PRODUCT EVIDENCE</p><h2>What the page actually states</h2></div><button className="text-button" onClick={() => setShowEvidence((value) => !value)}>{showEvidence ? 'Hide evidence' : 'Show evidence'}</button></div><div className="attribute-table">{product.attributes.map((attribute) => <div className={attribute.value ? 'attribute-row' : 'attribute-row unknown'} key={attribute.attribute_id}><span>{label(attribute.attribute_id)}</span><b>{attribute.value ?? '? Not found on page'}</b>{showEvidence && <small>{attribute.evidence ?? 'No supporting text was extracted.'}</small>}</div>)}</div></section>
          </>
        )}
      </section>
    </main>
  )
}

export default App
