import React from 'react';
import {ArrowUpRight, FileText, SlidersHorizontal, Check, Upload, ScanLine, AlertCircle, Wifi, WifiOff, Loader} from 'lucide-react';
import {resolveUrl, errorText} from './api';
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
