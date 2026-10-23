import React from 'react';
import {ArrowUpRight, FileText, SlidersHorizontal, Check, Upload, ScanLine, AlertCircle, Wifi, WifiOff, Loader} from 'lucide-react';
import {resolveUrl, errorText, breakLabel} from './api';
import {paragraphs,steps} from './data';
export function Brand(){return <a className="brand" href="#home">What the Doc?!<FileText aria-hidden="true"/></a>;}
export function Header({route}){return <header className="header"><Brand/><nav aria-label="Main navigation"><a href="#guide" aria-current={route==='guide'?'page':undefined}>How it works</a><a href="#history" aria-current={route==='history'?'page':undefined}>My documents</a><a className="nav-pill" href="#upload">Open workspace <ArrowUpRight/></a><a className="icon-button" href="#settings" aria-label="Settings"><SlidersHorizontal/></a></nav></header>;}
export function Footer({backend}){const live=backend?.state==='live',connected=live||backend?.state==='degraded';return <footer><div><Brand/><small>Less formatting. More living.</small></div><nav aria-label="Footer"><a href="#guide">How it works</a><a href="#privacy">Documents & privacy</a><a href="#design">Design system</a></nav><p>{connected?`Local API connected${live?` · ${backend.health?.model_runtime?.configured_model}`:' · no model'}`:'Frontend demo · No model connected.'}<br/>Made for the beautifully unbothered.</p></footer>;}
export function DemoBanner(){return <div className="demo-strip"><span className="dot"/> INTERACTIVE DEMO <span>/</span> Sample document · simulated analysis · no files uploaded</div>;}
/* The backend dropped out while a real document was open. The sample is still
 * reachable, but only as a deliberate choice: swapping it in automatically would put
 * a fabricated proposal on screen where the user's own document used to be. */
export function OfflineHold({documentName,hasJob,error,onRetry,onDemo}){
 return <div className="empty-panel"><WifiOff/>
  <h2>The local API stopped answering.</h2>
  <p>Your document is still open in this tab{documentName?<> — <strong>{documentName}</strong></>:null}, and nothing has been
  substituted for it. {hasJob?'Any job you started is still on the server; this tab just cannot reach it.':'It was never modified.'}</p>
  <p className="fineprint">{errorText(error)}</p>
  <div className="component-row"><Button onClick={onRetry}>Retry the connection</Button>
   <Button secondary onClick={onDemo}>Try the demo instead <ArrowUpRight/></Button></div>
  <p className="fineprint">The demo is a bundled sample with a simulated analysis. Opening it discards this document from the tab.</p></div>;
}
export function Steps(){const icons=[Upload,ScanLine,Check];return <div className="steps">{steps.map(([n,title,desc],i)=>{const Icon=icons[i];return <article key={n}><div className="step-art"><Icon/></div><div><span className="eyebrow">{n}</span><h3>{title}</h3><p>{desc}</p></div></article>;})}</div>;}
export function Badge({children,tone=''}){return <span className={`badge ${tone}`}>{children}</span>;}
export function Button({children,secondary=false,className='',...props}){return <button className={`button ${secondary?'outline':'primary'} ${className}`} {...props}>{children}</button>;}
export function PageHeading({eyebrow,children,description}){return <div className="page-heading"><span className="eyebrow">{eyebrow}</span><h1>{children}</h1>{description&&<p>{description}</p>}</div>;}
export function Paper({fixed=false,selected=1,onSelect,zoom=100}){return <article className="paper" style={{fontSize:`${zoom/100}rem`}} aria-label={fixed?'Proposed sample document':'Original sample document'}><div className="paper-top"><span>FIELD NOTES / 001</span><span>1</span></div><h2>A little less busy.</h2><p className="paper-deck">An exploration of thoughtful tools and everyday work.</p><hr/>{paragraphs.map((p,i)=><section key={p.title} className={`paper-paragraph ${i===selected?'target':''} ${i===1&&!fixed?'broken-spacing':''}`}><h3>{onSelect?<button onClick={()=>onSelect(i)} aria-pressed={selected===i}>{p.title}</button>:p.title}</h3><p>{p.text}</p>{i===1&&!fixed&&<span className="gap-bracket"><i>Too much space</i></span>}{i===1&&fixed&&<span className="fixed-mark" aria-label="Spacing adjusted">✓</span>}</section>)}<div className="paper-foot">ILLUSTRATIVE SAMPLE · NOT A WORD RENDER</div></article>;}
export function Empty({title,children,onSample}){return <div className="empty-panel"><FileText/><h2>{title}</h2><p>{children}</p><Button onClick={onSample}>Try a messy example <ArrowUpRight/></Button></div>;}

/* ---- Live backend components. These render what api.py returned, and nothing else. ---- */

const PT = (v)=>`${Number(v).toFixed(Number.isInteger(v)?0:2)} pt`;
/** A null value with origin application_default means NO LAYER SET IT, not zero. */
export function formatProperty(field,entry){
 if(!entry)return 'not reported';
 const {value,origin}=entry;
 if(value===null||value===undefined)return origin==='application_default'?'not set (Word default)':'not set';
 if(typeof value==='number'&&/_pt$/.test(field))return PT(value);
 return String(value);
}
export function originLabel(entry){
 const o=entry?.origin;
 if(!o)return '';
 if(o==='direct')return 'direct formatting';
 if(o==='application_default')return 'no layer set it';
 if(o.startsWith('style:'))return `inherited from ${o.slice(6)}`;
 return o;
}
export function PropertyList({paragraph}){
 const props=paragraph?.effective_properties||{};
 const keys=Object.keys(props);
 if(!keys.length)return null;
 return <div className="property-table">{keys.map(k=><div key={k}><span>{k.replace(/_/g,' ')}</span><strong>{formatProperty(k,props[k])}<small>{originLabel(props[k])}</small></strong></div>)}
  {paragraph?.resolution_caveats?.length?<div><span>caveats</span><strong>{paragraph.resolution_caveats.join(' · ')}</strong></div>:null}</div>;
}
export function ConnectionBanner({backend,onRetry}){
 const {state,health,error}=backend;
 if(state==='checking')return <div className="demo-strip"><span className="dot"/> CONNECTING <span>/</span> Looking for the local API…</div>;
 if(state==='offline')return <div className="demo-strip offline"><WifiOff/> BACKEND OFFLINE <span>/</span> {errorText(error)} <button className="text-button" onClick={onRetry}>Retry</button></div>;
 const live=state==='live';
 const model=health?.model_runtime?.configured_model;
 return <div className={`demo-strip ${live?'live':'warning'}`}><Wifi/> {live?'CONNECTED':'CONNECTED · NO MODEL'} <span>/</span>
  {live?`Gemma ${model} · ${health?.renderer?.available?'LibreOffice previews on':'no renderer: previews unavailable'}`:(health?.model_runtime?.reason||'Live diagnosis unavailable')}</div>;
}
export function ApiErrorNote({error,onDismiss}){
 if(!error)return null;
 return <p className="error" role="alert"><AlertCircle/> <strong>{error.code}</strong> — {errorText(error)}{onDismiss&&<button className="text-button" onClick={onDismiss}>Dismiss</button>}</p>;
}
export function JobProgress({job,elapsed,onStopWatching}){
 return <><ModeBadge job={job}/><span className="eyebrow">{job?.state==='queued'?'QUEUED':'RUNNING'}</span><h2>{job?.is_live_model_answer?<>Gemma is<br/>reading it.</>:<>Checking an<br/>injected fixture.</>}</h2><div className="spinner"/>
  <p role="status">Elapsed {Math.floor(elapsed/60)}m {String(elapsed%60).padStart(2,'0')}s</p>
  <p className="fineprint">{job?.is_live_model_answer?'CPU inference can take several minutes. The runtime reports no progress, so there is no progress bar to show.':'This developer-injected diagnosis does not contact a model. No model inference is being measured.'}</p>
  <Button secondary onClick={onStopWatching}>Stop watching</Button>
  <p className="fineprint">The job keeps running on the server; this only stops polling.</p></>;
}
export function PreviewPages({urls=[],label,comparison}){
 if(!urls.length)return <div className="preview-missing"><Loader/><p><strong>Not rendered.</strong> This job ran without LibreOffice previews. That is “not rendered”, not “no visible change”.</p></div>;
 return <div className="preview-pages" aria-label={label}>{urls.map((u,i)=><figure key={u}><img src={resolveUrl(u)} alt={`${label} page ${i+1}`} loading="lazy"/><figcaption>Page {i+1}{comparison?.[i]?.status==='compared'?` · ${(comparison[i].changed_ratio*100).toFixed(1)}% pixels changed`:''}</figcaption></figure>)}</div>;
}
export function InvariantList({job}){
 const inv=job?.invariants||{};
 const keys=Object.keys(inv);
 if(!keys.length)return null;
 return <ul className="checks">{keys.map(k=><li key={k} className={inv[k].passed?'':'failed'}>{inv[k].passed?<Check/>:<AlertCircle/>} {k.replace(/_/g,' ')}<small>{inv[k].detail}</small></li>)}</ul>;
}
export function ModeBadge({job}){
 if(!job)return null;
 return job.is_live_model_answer
  ? <Badge tone="quiet">LIVE MODEL ANSWER · {job.model}</Badge>
  : <Badge tone="warning"><AlertCircle/> FIXTURE — INJECTED BY A DEVELOPER, NOT A MODEL</Badge>;
}

/* ---- Explicit breaks. Everything here renders what /breaks returned. ---- */

/** The one selector for identified breaks.
 *
 * Two breaks in the same paragraph are the case this exists for, so each row carries
 * its ordinal AND the text either side of it: an id is not something a person can
 * tell apart. A break the engine will refuse is listed too, disabled, with the
 * server's own reason, because hiding it would make "why can't I pick that one?"
 * unanswerable. Nothing here is phrased as a recommendation: a deliberate break is
 * not a fault, and only the person reading the document can say which it is.
 */
export function BreakSelector({report, paragraphId, selectedId, onSelect, disabled=false}){
 if(!report) return null;
 const all=report.breaks||[];
 const here=all.filter(b=>b.paragraph_id===paragraphId);
 const elsewhere=all.length-here.length;
 if(!here.length) return <div className="break-panel"><span className="eyebrow">EXPLICIT BREAKS</span>
  <p className="fineprint">No page or column break element in this paragraph.{elsewhere?` ${elsewhere} elsewhere in the document.`:''} A “page break before” paragraph setting is a different thing and is listed with the properties above.</p></div>;
 return <div className="break-panel"><span className="eyebrow">EXPLICIT BREAKS IN THIS PARAGRAPH</span>
  <ul className="break-list">
   {here.map((b,i)=>{
    const key=b.target_id||`lrpb-${i}`;
    const pickable=b.edit_eligible&&!disabled;
    return <li key={key} className={`break-item ${selectedId===b.target_id?'selected':''} ${b.edit_eligible?'':'blocked'}`}>
     <button type="button" disabled={!pickable} aria-pressed={selectedId===b.target_id}
      onClick={()=>onSelect?.(selectedId===b.target_id?null:b.target_id)}>
      <span className="break-head">
       <strong>{b.kind==='last_rendered_page_break'?'Last rendered page break':breakLabel(b)}</strong>
       <Badge tone={b.edit_eligible?'':'warning'}>{b.edit_eligible?(b.break_type||'').toUpperCase():'NOT REMOVABLE'}</Badge>
      </span>
      <span className="break-context">
       <i>…{b.text_before||'(start of paragraph)'}</i>
       <em aria-label="the break itself">↵ break ↵</em>
       <i>{b.text_after||'(end of paragraph)'}…</i>
      </span>
      {b.edit_eligible?null:<small className="break-reason">{b.ineligible_reason}</small>}
     </button></li>;})}
  </ul>
  <p className="fineprint">{elsewhere?`${elsewhere} more break${elsewhere===1?'':'s'} elsewhere in this document. `:''}Selecting a break says which one you mean. It is not a request to remove it, and a break you inserted on purpose is not a fault.</p></div>;
}

/** What a proposal is actually about to delete, shown before any approval. */
export function BreakTargetList({job}){
 const targets=job?.break_targets;
 if(!targets?.length) return null;
 return <div className="break-panel"><span className="eyebrow">THE BREAK BEING REMOVED</span>
  {targets.map((t,i)=><div className="break-target" key={t.target_id||i}>
   <strong>{breakLabel(t)}</strong>
   <span className="break-context"><i>…{t.text_before||'(start of paragraph)'}</i><em>↵ this break ↵</em><i>{t.text_after||'(end of paragraph)'}…</i></span>
   <small>Only this <code>w:br</code> element is deleted. The run holding it, its formatting, the text either side and every other break stay as they are.</small>
  </div>)}</div>;
}

/** Page counts before and after, stated without promising a reduction. */
export function PageCountChange({job}){
 const c=job?.visual_comparison;
 if(!c) return null;
 const delta=c.page_count_delta;
 return <p className="page-delta"><strong>{c.before_pages} page{c.before_pages===1?'':'s'} → {c.after_pages} page{c.after_pages===1?'':'s'}</strong>{' '}
  {delta===0
   ?'The page count is unchanged. The document reflowed without losing a page, which is a normal result: removing a break does not have to change how many pages there are.'
   :`${delta<0?'One or more pages fewer':'More pages'} (${delta>0?'+':''}${delta}). A page-count change is something to look at, not an approval.`}</p>;
}

/** Section boundaries. One conversion is offered; everything else is read-only. */
export function SectionList({report,selectedId,onSelect,disabled=false}){
 const sections=report?.sections;
 if(!sections?.length) return null;
 const any=sections.some(s=>s.edit_eligible);
 const describe=s=><>
   <strong>Section {s.section_index+1} · starts {s.start_type}{s.start_type_is_default?' (schema default; no w:type written)':''}</strong>
   <small>{s.governs}. {s.columns?.count===1?'Single column':`${s.columns.count} columns`}. Defined in {s.defined_in==='body'?'the body’s final sectPr':'that paragraph’s properties'}.</small>
  </>;
 return <div className="break-panel"><span className="eyebrow">SECTIONS{any?'':' · NOTHING CONVERTIBLE HERE'}</span>
  <ul className="section-list">{sections.map(s=><li key={s.section_index}>
   {s.edit_eligible&&onSelect
    ?<button type="button" className={`break-option ${selectedId===s.target_id?'selected':''}`} disabled={disabled}
       aria-pressed={selectedId===s.target_id}
       onClick={()=>onSelect(selectedId===s.target_id?null:s.target_id)}>
      {describe(s)}
      <Badge>CAN BECOME CONTINUOUS</Badge>
     </button>
    :<>{describe(s)}{s.ineligible_reason?<small className="break-reason">{s.ineligible_reason}</small>:null}</>}
  </li>)}</ul>
  <p className="fineprint">Only one section change exists: a <code>nextPage</code> start can be asked to become <code>continuous</code>. Columns, page size, margins, numbering, headers, footers and the section boundaries themselves cannot be changed. Selecting a section says which one you mean; it is not a request to change it, and a section break you inserted on purpose is not a fault. Paragraph numbers here are body-paragraph ids, not page numbers.</p></div>;
}

/** What a section proposal is actually about to change, shown before any approval. */
export function SectionTargetList({job}){
 const targets=job?.section_targets;
 if(!targets?.length) return null;
 return <div className="break-panel"><span className="eyebrow">THE SECTION BEING CHANGED</span>
  {targets.map((t,i)=><div className="break-target" key={t.target_id||i}>
   <strong>Section {(t.section_index??0)+1} · {t.start_type} → continuous</strong>
   <span className="break-context"><i>{t.governs}</i></span>
   <small>Only the <code>w:type</code> of this one <code>w:sectPr</code> is rewritten. The number of sections, where each one begins and ends, its columns, page size, margins, numbering, headers and footers all stay exactly as they are. Continuous asks the section to continue on the same page; it does not guarantee a page disappears, because page geometry and other settings can still force one.</small>
  </div>)}</div>;
}
