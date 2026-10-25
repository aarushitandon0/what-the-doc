import React,{useState,useEffect,useRef,useCallback} from 'react';
import {ArrowUpRight,ArrowLeft,Check,FileText,Upload,Plus,Minus,X,ShieldCheck,Download,Clock,AlertCircle} from 'lucide-react';
import {Header,Footer,DemoBanner,Steps,Badge,Button,PageHeading,Paper,Empty,ConnectionBanner,ApiErrorNote,JobProgress,PreviewPages,PropertyList,InvariantList,ModeBadge,OfflineHold,BreakSelector,BreakTargetList,KeptBreaks,PageCountChange,SectionList,SectionTargetList,DocxPages,TechDetails} from './components';
import {paragraphs,validateFile,readHistory,report} from './data';
import * as api from './api';

/* The live review heading names the kind of change actually under review; the
 * spacing line belongs to the bundled spacing sample only. */
const reviewHeadline={paragraph_property:'One paragraph setting changed.',explicit_break_removal:'One break removed.',section_start_change:'One section start changed.'};
const pageCount=(n)=>n==null?'? pages':`${n} page${n===1?'':'s'}`;
export default function App(){
 const [route,setRoute]=useState(()=>window.location.hash.slice(1)||'home');
 const [selected,setSelected]=useState(1),[complaint,setComplaint]=useState('There’s too much space above this heading.');
 const [status,setStatus]=useState('idle'),[view,setView]=useState('side'),[zoom,setZoom]=useState(100);
 const [file,setFile]=useState(null),[fileError,setFileError]=useState(''),[dragging,setDragging]=useState(false);
 const [history,setHistory]=useState(readHistory),[motion,setMotion]=useState(true),[notice,setNotice]=useState('');
 const [demoChosen,setDemoChosen]=useState(false);
 const fileRef=useRef(null),mainRef=useRef(null),timer=useRef(null),toastTimer=useRef(null),shownRoute=useRef(null);
 const proposed=['awaiting_review','accepted'].includes(status);
 const go=(r)=>{window.location.hash=r;};
 useEffect(()=>{const change=()=>setRoute(window.location.hash.slice(1)||'home');window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change);},[]);
 useEffect(()=>{document.title=`${route==='home'?'Less formatting. More living.':route.charAt(0).toUpperCase()+route.slice(1)} — What the Doc?!`;/* A route change in a single-page app announces nothing, so focus moves to the new
  * main region. The first paint is not a route change: focusing main there would take
  * the skip link's own tab stop away and leave it unreachable by keyboard. The guard
  * compares the route actually shown rather than counting renders, because StrictMode
  * runs this effect twice on mount and a "first render" flag would be spent by the
  * first pass and then steal focus on the second. */
 const moved=shownRoute.current!==null&&shownRoute.current!==route;shownRoute.current=route;
 if(moved)mainRef.current?.focus({preventScroll:true});window.scrollTo?.(0,0);if(route!=='workspace'){clearTimeout(timer.current);setStatus(s=>s==='running'?'idle':s);}},[route]);
 useEffect(()=>()=>{clearTimeout(timer.current);clearTimeout(toastTimer.current);},[]);
 const notify=(text)=>{setNotice(text);clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setNotice(''),4000);};
 const storeHistory=(items)=>{setHistory(items);try{localStorage.setItem('wtd-demo-history',JSON.stringify(items));}catch{notify('Storage unavailable. History will last for this session.');}};
 const sample=()=>{clearTimeout(timer.current);setDemoChosen(true);setStatus('idle');setSelected(1);setComplaint('There’s too much space above this heading.');setZoom(100);go('workspace');};
 const choose=(f)=>{const error=validateFile(f);setFileError(error||'');if(!error)setFile(f);};
 const diagnose=()=>{if(!complaint.trim()){notify('Tell us what looks wrong first.');document.getElementById('complaint')?.focus();return;}if(selected!==1||!/space|gap|spacing/i.test(complaint)){setStatus('needs_clarification');go('clarify');return;}setStatus('running');timer.current=setTimeout(()=>{setStatus('awaiting_review');timer.current=null;},1800);};
 const reject=()=>{setStatus('rejected');go('workspace');notify('Original kept. No changes applied.');};
 const accept=()=>{if(status!=='awaiting_review')return;setStatus('accepted');storeHistory([{id:crypto.randomUUID(),date:new Date().toISOString()},...history].slice(0,20));go('results');};
 const download=()=>{if(status!=='accepted')return;const url=URL.createObjectURL(new Blob([JSON.stringify({...report,accepted:true},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='what-the-doc-demo-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Demo report downloaded. No DOCX was generated.');};
 const selectParagraph=i=>{if(status==='running')return;setSelected(i);setStatus('idle');};

 /* ---------------- live backend ---------------- */
 const [backend,setBackend]=useState({state:'checking',health:null,error:null});
 const [doc,setDoc]=useState(null),[docParagraphs,setDocParagraphs]=useState([]);
 const [liveSelected,setLiveSelected]=useState(null),[job,setJob]=useState(null);
 /* Structural targets are server-issued, never composed here. `selectedBreak` and
  * `selectedSection` each hold a target_id the server minted for THIS revision, and
  * both are cleared whenever the document or the paragraph changes, so a stale id
  * can never be sent. They are mutually exclusive: a break and a section are
  * different repairs, approved separately, and the server refuses a job naming
  * both. */
 const [breaks,setBreaks]=useState(null),[sections,setSections]=useState(null),[selectedBreak,setSelectedBreak]=useState(null),[selectedSection,setSelectedSection]=useState(null);
 /* The uploaded document rendered to pages, before anything is proposed. Held
  * separately from `job.preview`: this is the document as it stands, not a
  * comparison, and it stays valid while jobs come and go because the original is
  * never written to. `null` means "not asked yet", which the canvas shows as
  * loading rather than as "no render". */
 const [docPreview,setDocPreview]=useState(null),[docView,setDocView]=useState('pages');
 /* The original file itself, for the instant browser preview. `docSourceFailed` holds the
  * reason the browser could not draw it, which is when the LibreOffice render is asked
  * for instead. */
 const [docSource,setDocSource]=useState(null),[docSourceFailed,setDocSourceFailed]=useState(null);
 /* Whole-document check: the same complaint asked about each paragraph in turn, one
  * job each. `wholeDoc` is the selection; `scan` is the run and its results. */
 const [wholeDoc,setWholeDoc]=useState(false),[scan,setScan]=useState(null);
 const scanStop=useRef(false),scanAbort=useRef(null),openDocId=useRef(null);
 const [busy,setBusy]=useState(''),[apiError,setApiError]=useState(null),[elapsed,setElapsed]=useState(0);
 const [jobs,setJobs]=useState([]);
 const abort=useRef(null),clock=useRef(null);
 const connected=backend.state==='live'||backend.state==='degraded';
 const liveParagraph=docParagraphs.find(p=>p.paragraph_id===liveSelected)||null;
 const fail=(error)=>{if(error?.name==='AbortError')return;setApiError(error instanceof api.ApiError?error:new api.ApiError(String(error?.message||error)));if(error?.code==='backend_unreachable')setBackend({state:'offline',health:null,error});};

 const checkBackend=useCallback(async()=>{
  setBackend(b=>({...b,state:'checking'}));
  try{
   const health=await api.health();
   setBackend({state:health.live_diagnosis_available?'live':'degraded',health,error:null});
  }catch(error){setBackend({state:'offline',health:null,error});}
 },[]);
 useEffect(()=>{checkBackend();},[checkBackend]);
 useEffect(()=>()=>{abort.current?.abort();clearInterval(clock.current);},[]);

 const loadParagraphs=async(documentId)=>{
  const body=await api.getParagraphs(documentId);
  setDocParagraphs(body.paragraphs||[]);
  /* Nothing is selected for the person. Pre-selecting the first paragraph sent
   * complaints about some other paragraph to the title, where they could only fail. */
  setLiveSelected(null);
  /* Breaks and sections come from the server for this revision. A failure here is
   * reported and leaves them null: the UI then says nothing was inspected rather
   * than implying the document has no breaks. */
  setBreaks(null);setSections(null);setSelectedBreak(null);
  try{setBreaks(await api.getBreaks(documentId));}catch(error){fail(error);}
  try{setSections(await api.getSections(documentId));}catch(error){fail(error);}
 };
 /* Rendering costs seconds of LibreOffice, so it is never awaited on the way into
  * the workspace: the outline and text appear immediately and the pages replace them
  * when they arrive. A failure here is not a failed document - the diagnosis path is
  * structural and works without a render - so it degrades to the text view and says
  * why, rather than surfacing as an API error. */
 /* A render takes seconds, so a second document can be opened before the first one's
  * pages arrive. Only the answer for the document currently open is kept. */
 const previewFor=useRef(null);
 const loadDocPreview=async(documentId)=>{
  previewFor.current=documentId;setDocPreview(null);
  let result;
  try{result=await api.getDocumentPreview(documentId);}
  catch(error){if(error?.name==='AbortError')return;result={rendered:false,page_count:0,urls:[],unavailable_reason:api.errorText(error)};}
  if(previewFor.current===documentId)setDocPreview(result);
 };
 /* The browser could not draw the file, so the slower LibreOffice render is the
  * fallback. Only for the document still open. */
 const browserPreviewFailed=(documentId,reason)=>{
  if(openDocId.current!==documentId)return;
  setDocSourceFailed(reason||'the browser could not draw this file');
  loadDocPreview(documentId);
 };
 /* A document opened from this tab's upload is drawn from the File already in memory,
  * with no round trip; anything else (the sample) is fetched once. */
 const loadOriginal=async(documentId)=>{
  try{const blob=await api.getOriginal(documentId);if(openDocId.current===documentId)setDocSource(blob);}
  catch(error){if(error?.name!=='AbortError')browserPreviewFailed(documentId,api.errorText(error));}
 };
 const openDocument=async(record,localFile=null)=>{
  previewFor.current=null;setDocPreview(null);
  openDocId.current=record.document_id;setDocSource(localFile);setDocSourceFailed(null);
  scanStop.current=true;scanAbort.current?.abort();setScan(null);setWholeDoc(false);
  setDemoChosen(false);setDoc(record);setJob(null);setApiError(null);setSelectedBreak(null);setSelectedSection(null);setDocView('pages');
  try{await loadParagraphs(record.document_id);go('workspace');if(!localFile)loadOriginal(record.document_id);}catch(error){fail(error);}
 };
 const uploadReal=async(f)=>{
  const message=validateFile(f);
  if(message){setFileError(message);return;}
  setFileError('');setFile(f);setBusy('uploading');setApiError(null);
  import('docx-preview').catch(()=>{});// fetched while the upload runs, so drawing starts at once
  try{await openDocument(await api.uploadDocument(f),f);}catch(error){fail(error);}finally{setBusy('');}
 };
 const openDemoDocument=async()=>{
  setBusy('demo');setApiError(null);
  try{await openDocument(await api.demoDocument());}catch(error){fail(error);}finally{setBusy('');}
 };
 const watch=async(started)=>{
  abort.current?.abort();
  const controller=new AbortController();abort.current=controller;
  const startedAt=Date.now();
  clearInterval(clock.current);setElapsed(0);
  clock.current=setInterval(()=>setElapsed(Math.floor((Date.now()-startedAt)/1000)),1000);
  try{
   const finished=await api.pollJob(started.job_id,{signal:controller.signal,onUpdate:setJob});
   setJob(finished);
   if(finished.state==='needs_clarification')go('clarify');
   else if(finished.state==='awaiting_review'){
    // Show the proposed pages straight away; the original stays one click away.
    if(finished.preview?.rendered&&finished.preview.after_urls?.length)setDocView('after');
    notify('A candidate is ready for review. Completing a job is not accepting it.');
   }
  }catch(error){fail(error);}
  finally{clearInterval(clock.current);}
 };
 const diagnoseLive=async()=>{
  if(!complaint.trim()){notify('Tell us what looks wrong first.');document.getElementById('complaint')?.focus();return;}
  if(liveSelected===null){notify('Select a paragraph first.');return;}
  setApiError(null);setBusy('diagnosing');
  try{
   const started=await api.createJob({documentId:doc.document_id,paragraphId:liveSelected,complaint,render:backend.health?.renderer?.available!==false,expectedSourceSha256:doc.source_sha256,breakTargetId:selectedBreak,sectionTargetId:selectedSection});
   setJob(started);go('workspace');watch(started);
  }catch(error){fail(error);}finally{setBusy('');}
 };
 /* Whole document: the complaint is asked about every paragraph that has text, one job
  * after another, through exactly the same endpoint and checks as a single paragraph.
  * Nothing is combined or applied: each result is opened and reviewed on its own. */
 const scanTargets=docParagraphs.filter(p=>p.text?.trim());
 const syncScan=(updated)=>setScan(s=>s&&{...s,items:s.items.map(it=>it.job?.job_id===updated.job_id?{...it,job:updated,state:updated.state}:it)});
 const runScan=async()=>{
  if(!complaint.trim()){notify('Tell us what looks wrong first.');document.getElementById('complaint')?.focus();return;}
  if(!scanTargets.length){notify('This document has no paragraphs with text to check.');return;}
  const documentId=doc.document_id,text=complaint;
  /* Unwanted page breaks are fixed across the whole document in ONE candidate: the
   * server lists every break itself, so no paragraph-by-paragraph guessing and no
   * separate fixes to stitch together. Anything else falls through to the scan. */
  setApiError(null);setBusy('diagnosing');
  try{
   const started=await api.createJob({documentId,complaint:text,scope:'document',render:backend.health?.renderer?.available!==false,expectedSourceSha256:doc.source_sha256});
   setScan(null);setWholeDoc(false);setLiveSelected(null);setSelectedBreak(null);setSelectedSection(null);
   setJob(started);setBusy('');watch(started);return;
  }catch(error){
   setBusy('');
   if(error?.code!=='no_document_repair'){fail(error);return;}
  }
  abort.current?.abort();scanAbort.current?.abort();
  const controller=new AbortController();scanAbort.current=controller;scanStop.current=false;
  const items=scanTargets.map(p=>({paragraph_id:p.paragraph_id,state:'waiting',job:null,error:null}));
  const update=(i,patch)=>setScan(s=>s&&s.documentId===documentId?{...s,items:s.items.map((it,k)=>k===i?{...it,...patch}:it)}:s);
  setApiError(null);setJob(null);
  setScan({documentId,complaint:text,items,running:true,skipped:docParagraphs.length-items.length,startedAt:Date.now()});
  const startedAt=Date.now();clearInterval(clock.current);setElapsed(0);
  clock.current=setInterval(()=>setElapsed(Math.floor((Date.now()-startedAt)/1000)),1000);
  for(let i=0;i<items.length;i++){
   if(scanStop.current)break;
   update(i,{state:'running'});
   try{
    const started=await api.createJob({documentId,paragraphId:items[i].paragraph_id,complaint:text,render:backend.health?.renderer?.available!==false,expectedSourceSha256:doc.source_sha256},controller.signal);
    update(i,{job:started});
    const finished=await api.pollJob(started.job_id,{signal:controller.signal});
    update(i,{state:finished.state,job:finished});
   }catch(error){
    if(error?.name==='AbortError'){update(i,{state:'stopped'});break;}
    update(i,{state:'error',error:api.errorText(error)});
    // Conditions that will refuse every later paragraph too end the run.
    if(['backend_unreachable','model_unavailable','not_found'].includes(error?.code)){fail(error);break;}
   }
  }
  clearInterval(clock.current);
  setScan(s=>s&&s.documentId===documentId?{...s,running:false,items:s.items.map(it=>it.state==='waiting'?{...it,state:'not_checked'}:it)}:s);
 };
 /* Stopping ends the run after the paragraph in progress; the server cannot cancel a
  * model call already under way, and that one job still finishes on its own. */
 const stopScan=()=>{scanStop.current=true;scanAbort.current?.abort();};
 const openScanResult=(item)=>{
  if(!item.job||api.isPending(item.job))return;
  setWholeDoc(false);setLiveSelected(item.paragraph_id);setSelectedBreak(null);setSelectedSection(null);setJob(item.job);
  setDocView(item.job.preview?.rendered&&item.job.preview.after_urls?.length&&['awaiting_review','accepted'].includes(item.job.state)?'after':'pages');
 };
 const backToScan=()=>{setJob(null);setWholeDoc(true);setDocView('pages');};
 const acceptLive=async()=>{
  if(job?.state!=='awaiting_review')return;
  setBusy('accepting');setApiError(null);
  try{
   const updated=await api.acceptJob(job.job_id,job.candidate?.accept_requires_candidate_sha256||job.candidate?.candidate_sha256);
   setJob(updated);syncScan(updated);go('results');
  }catch(error){fail(error);}finally{setBusy('');}
 };
 const rejectLive=async()=>{
  if(job?.state!=='awaiting_review')return;
  setBusy('rejecting');setApiError(null);
  try{const updated=await api.rejectJob(job.job_id,'reviewer kept the original');setJob(updated);syncScan(updated);go('workspace');notify('Original kept. The candidate stays on disk for inspection.');}
  catch(error){fail(error);}finally{setBusy('');}
 };
 const loadJobs=useCallback(async()=>{
  try{const body=await api.listJobs();setJobs(body.jobs||[]);}catch(error){fail(error);}
 },[]);
 useEffect(()=>{if(connected&&route==='history')loadJobs();},[connected,route,loadJobs]);

 const stateNote={queued:'Waiting for the worker.',running:'The pipeline is executing.',needs_clarification:'Gemma needs a clearer complaint.',no_supported_repair:'Nothing supported to repair here.',failed:'The run failed.',awaiting_review:'A candidate exists and is NOT accepted.',accepted:'Accepted by a human reviewer.',rejected:'Rejected. The candidate stays on disk.'};

 const wrap=(content,cls='standard-main',banner=false,foot=true,demoStrip=true)=><>{<Header route={route}/>} {banner&&<><ConnectionBanner backend={backend} onRetry={checkBackend}/>{connected||!demoStrip?null:<DemoBanner/>}</>}<main id="main" ref={mainRef} tabIndex={-1} className={cls}>{content}</main>{foot&&<Footer backend={backend}/>}</>;

 /* ---------------- live pages ---------------- */
 const LIVE_ROUTES=['upload','workspace','review','results','clarify','history'];
 const paragraphLabel=(p)=>p.text?.trim()||'(empty paragraph)';
 const proposalRows=(job?.proposal?.operations)||[];
 /* Three operation shapes exist and every summary has to cope with all of them:
    a paragraph property, a break removal, and a section-start change. */
 const opLabel=op=>op.operation==='change_section_start'?'section start'
   :op.operation==='remove_explicit_break'?'remove break'
   :`¶${op.paragraph_id} · ${String(op.field||'').replace(/_/g,' ')}`;
 const opValue=op=>op.operation==='change_section_start'
   ?`${op.expected_start_type} → ${op.new_start_type}`
   :op.operation==='remove_explicit_break'?`${op.expected_break_type} break`
   :String(op.value);
 const opSentence=op=>op.operation==='change_section_start'
   ?`one section start ${op.expected_start_type} → ${op.new_start_type}`
   :op.operation==='remove_explicit_break'
   ?`remove one ${op.expected_break_type} break`
   :`¶${op.paragraph_id} ${String(op.field||'').replace(/_/g,' ')} → ${op.value}`;
 const previewReady=job?.preview?.rendered;
 /* The pages of the uploaded document itself. A finished job re-renders the same
  * original, so its "before" pages are preferred once they exist - one render, and
  * the picture in the workspace is literally the one the review compares against. */
 const docPages=(previewReady&&job.preview.before_urls?.length?job.preview.before_urls:docPreview?.urls)||[];
 /* The candidate's pages, once a job has rendered one. They are a proposal until a
  * person accepts them, and the canvas label says which of the two it is showing. */
 const afterPages=(previewReady&&['awaiting_review','accepted'].includes(job?.state)&&job.preview.after_urls)||[];
 /* The browser-drawn original, used until a job has LibreOffice pages to compare. A
  * comparison keeps both sides from one renderer, so it never mixes the two. */
 const quickPreview=Boolean(docSource)&&!docSourceFailed&&!afterPages.length;
 const canvasView=docView==='after'&&!afterPages.length?'pages':docView;
 const viewOptions=afterPages.length?[['pages','Before'],['after','After'],['text','Text']]:[['pages','Pages'],['text','Text']];
 /* One way to change the selected paragraph, from the outline or the text view. A
  * break or section id belongs to one paragraph, so both are cleared with it. */
 const selectLive=(paragraphId)=>{
  if(api.isPending(job)||scan?.running||(paragraphId===liveSelected&&!wholeDoc))return;
  setWholeDoc(false);setLiveSelected(paragraphId);setJob(null);setSelectedBreak(null);setSelectedSection(null);
 };
 const selectWhole=()=>{
  if(api.isPending(job)||scan?.running)return;
  setWholeDoc(true);setLiveSelected(null);setJob(null);setSelectedBreak(null);setSelectedSection(null);setDocView('pages');
 };
 const scanBusy=Boolean(scan?.running);
 const breaksHere=(breaks?.breaks||[]).filter(b=>b.paragraph_id===liveSelected&&b.edit_eligible);
 const renderUnavailable=backend.health?.renderer?.available===false;
 /* A structural change the server will not accept unrendered. The button is disabled
  * as a courtesy; the refusal itself lives in api.py and workflow.py. */
 const structuralUnrendered=Boolean(job?.rendering_required&&!job?.rendering_satisfied);

 const liveUpload=()=><><a className="backlink" href="#home"><ArrowLeft/> Back to the good life</a>
  <PageHeading eyebrow="LET’S TAKE A LOOK" description="Your document goes to the local API on this machine, is opened with engine.open_package and is never modified.">Drop the document.<br/>Keep your cool.</PageHeading>
  <ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>
  <div className="upload-layout">
   <section className={`dropzone ${dragging?'dragging':''}`} onDragOver={e=>{e.preventDefault();setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false);uploadReal(e.dataTransfer.files[0]);}}>
    <div className="upload-emblem"><Upload/></div><h2>Your document goes here.</h2><p>Drop a .docx file, or choose one below.</p>
    <Button disabled={busy==='uploading'} onClick={()=>fileRef.current?.click()}>{busy==='uploading'?'Uploading…':<>Choose a document <Plus/></>}</Button>
    <input ref={fileRef} aria-label="Choose a Word document" type="file" accept=".docx" className="visually-hidden" onChange={e=>uploadReal(e.target.files[0])}/>
    <small>.docx only · up to 20 MB · tracked changes, content controls, macros and signed packages are refused by the backend</small>
    {fileError&&<p className="error" role="alert">{fileError}</p>}
   </section>
   <aside className="upload-aside"><span className="eyebrow">JUST LOOKING AROUND?</span>
    <div className="sample-paper"><FileText/><strong>THE MESSY<br/>REPORT</strong><i>Synthetic fixture · planted faults</i></div>
    <h3>Build the synthetic sample.</h3><p>The server builds it from fixtures.py and labels it a demo document. Diagnoses of it are still live model answers.</p>
    <Button secondary disabled={busy==='demo'} onClick={openDemoDocument}>{busy==='demo'?'Building…':<>Build the sample <ArrowUpRight/></>}</Button>
   </aside></div>
  <div className="reassurance"><span><ShieldCheck/> Original never modified</span><span><Check/> You review every change</span><span><FileText/> Repaired .docx on acceptance</span></div></>;

 const inspectorBody=()=>{
  if(api.isPending(job))return <JobProgress job={job} elapsed={elapsed} onStopWatching={()=>abort.current?.abort()}/>;
  if(job?.state==='awaiting_review')return <><ModeBadge job={job}/><h2>A candidate<br/>to review.</h2>
   <p>{job.diagnosis?.explanation}</p>
   <div className="property-table">{proposalRows.map((op,i)=><div key={i}><span>{opLabel(op)}</span><strong>{opValue(op)}</strong></div>)}</div>
   <BreakTargetList job={job}/>
   <KeptBreaks job={job}/>
   <SectionTargetList job={job}/>
   <InvariantList job={job}/>
   <p className="fineprint">Reaching this state is not approval. Nothing of yours has been changed.</p>
   <a className="button primary full" href="#review">Review the change <ArrowUpRight/></a>
   <button className="text-button" onClick={rejectLive} disabled={busy==='rejecting'}>Keep the original</button></>;
  /* An accepted job is the one state with a file to hand back, so the download sits
   * here as well as on the results page. A further fix starts from the original
   * again: the workspace never edits the file it was given. */
  if(job?.state==='accepted')return <><Badge><Check/> ACCEPTED</Badge><h2>Repair<br/>accepted.</h2>
   <p>{job.diagnosis?.explanation}</p>
   <div className="property-table">{proposalRows.map((op,i)=><div key={i}><span>{opLabel(op)}</span><strong>{opValue(op)}</strong></div>)}</div>
   <a className="button primary full" href={api.downloadUrl(job.job_id)} download>Download repaired .docx <Download/></a>
   <a className="text-button" href="#review">Compare before and after</a>
   <p className="fineprint">Your original is untouched. Another fix here starts from the original again; to build on this repair, upload the downloaded file.</p>
   <Button secondary onClick={()=>{setJob(null);setDocView('pages');}}>Fix something else</Button></>;
  if(job?.state==='no_supported_repair')return <><ModeBadge job={job}/>{job.answered_by!=='rule'&&<Badge tone="warning"><AlertCircle/> {job.diagnosis?.status?.toUpperCase()}</Badge>}<h2>{job.diagnosis?.status==='no_change'?<>Nothing wrong<br/>here.</>:<>Nothing<br/>to apply.</>}</h2><p>{job.diagnosis?.explanation}</p><p className="fineprint">{job.answered_by==='rule'?'Answered instantly from the document’s own settings. Nothing was changed.':stateNote[job.state]}</p><Button onClick={()=>setJob(null)}>Describe something else</Button></>;
  /* Gemma answered, but the safety checks threw the answer away. That is the checks
   * working, not the app breaking, so it is said plainly rather than as a crash. */
  if(api.isRefusal(job))return <><ModeBadge job={job}/><Badge tone="quiet">NOTHING CHANGED</Badge><h2>No safe fix<br/>found.</h2>
   <p>Gemma suggested something for ¶{job.paragraph_id}, but it did not pass the safety checks, so it was thrown away.</p>
   <p className="fineprint">{api.refusalText(job.error_detail)}</p>
   <p className="fineprint">Asking the same thing again usually gets the same answer. Try describing what you see in other words, or pick the paragraph where the problem actually is.</p>
   <p className="fineprint">Your original document is untouched.</p>
   <Button onClick={()=>setJob(null)}>Describe it differently</Button>
   <TechDetails job={job}/></>;
  if(job?.state==='failed')return <><ModeBadge job={job}/><Badge tone="warning"><AlertCircle/> {job.error_code}</Badge><h2>That run<br/>failed.</h2>
   <p>{api.ERROR_TEXT[job.error_code]||job.error_detail}</p>
   {/* The server's own words, kept verbatim and marked as such. Not attributed to
     * the validator: the same codes also cover timeouts, runtime errors and renders. */}
   {job.error_detail&&<p className="fineprint">Reason recorded: “{job.error_detail}”.</p>}
   {/* A run can fail after a candidate was built (a render or invariant failure), so
     * the claim is about the original, which is never written to. */}
   <p className="fineprint">Your original document is untouched.</p>
   <Button onClick={()=>setJob(null)}>Try again</Button></>;
  if(job?.state==='rejected')return <><Badge tone="quiet">Original kept</Badge><h2>Rejected.</h2><p>The candidate stays on disk for inspection and cannot be accepted later.</p><Button onClick={()=>setJob(null)}>Describe something else</Button></>;
  return <><span className="eyebrow">TELL US WHAT’S OFF</span><h2>What the<br/>document?</h2>
   <ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>
   {liveSelected===null&&<p className="pick-hint">First, click the paragraph that looks wrong in the list on the left — or choose <button className="text-button inline" onClick={selectWhole} disabled={scanBusy}>Whole document</button> to check every paragraph.</p>}
   {complaintField()}
   <div className="selection-detail"><span className="eyebrow">SELECTED PARAGRAPH</span><strong>{liveParagraph?`¶${liveParagraph.paragraph_id} · ${liveParagraph.style_id||'no style'}`:'None selected'}</strong><small>{liveParagraph?paragraphLabel(liveParagraph).slice(0,90):'Choose a paragraph in the outline.'}</small></div>
   <PropertyList paragraph={liveParagraph}/>
   <BreakSelector report={breaks} paragraphId={liveSelected} selectedId={selectedBreak} onSelect={id=>{setSelectedBreak(id);if(id)setSelectedSection(null);}} disabled={api.isPending(job)}/>
   {breaksHere.length>1&&!selectedBreak&&<p className="fineprint">This paragraph has {breaksHere.length} removable breaks. Pick the one you mean, or say which in your own words — nothing will guess for you.</p>}
   {renderUnavailable&&breaksHere.length>0&&<p className="fineprint">LibreOffice is unavailable, so a break removal cannot be reviewed and the server will refuse it. Paragraph spacing, indent and alignment repairs still work.</p>}
   <Button className="full" disabled={backend.state!=='live'||busy==='diagnosing'||liveSelected===null||scanBusy} onClick={diagnoseLive}>{busy==='diagnosing'?'Starting…':liveSelected===null?'Pick a paragraph first':<>Diagnose with Gemma <ArrowUpRight/></>}</Button>
   {backend.state!=='live'&&<p className="fineprint">Live diagnosis is unavailable: {backend.health?.model_runtime?.reason||'no Gemma runtime'}. Nothing is substituted in its place.</p>}
   <p className="fineprint">Takes 1–3 minutes on this computer. One check runs at a time.</p></>;
 };
 const complaintField=()=><>
  <label className="field-label" htmlFor="complaint">What looks wrong?</label>
  <textarea id="complaint" rows={4} value={complaint} disabled={scanBusy} onChange={e=>setComplaint(e.target.value)} placeholder="This heading is miles from the paragraph above it."/>
  <div className="chips">{['Too much space above this heading','This paragraph is not aligned like the others','This heading keeps jumping to a new page','There is a page break here I did not want','This column break splits the text oddly'].map(t=><button key={t} disabled={scanBusy} onClick={()=>setComplaint(t)}>{t}</button>)}</div></>;
 /* What each whole-document row says. A refused answer is not a crash, so it reads the
  * same as on a single paragraph. */
 const scanLabel={waiting:'Waiting',running:'Checking…',awaiting_review:'Fix ready to review',accepted:'Fix accepted',rejected:'Original kept',no_supported_repair:'Nothing to fix',needs_clarification:'Gemma has a question',stopped:'Stopped',not_checked:'Not checked',error:'Could not check'};
 const scanText=it=>it.state==='failed'?(api.isRefusal(it.job)?'No safe fix found':'Failed'):scanLabel[it.state]||it.state;
 const scanTone=it=>['awaiting_review','accepted'].includes(it.state)?'good':it.state==='running'?'busy':['error','failed'].includes(it.state)&&!api.isRefusal(it.job)?'bad':'quiet';
 const scanPanel=()=>{
  const n=scanTargets.length,skipped=docParagraphs.length-n;
  if(!scan)return <><span className="eyebrow">WHOLE DOCUMENT</span><h2>Check every<br/>paragraph.</h2>
   <ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>
   {complaintField()}
   <p className="fineprint">Gemma checks the {n} paragraph{n===1?'':'s'} with text one at a time{skipped?` (${skipped} empty one${skipped===1?' is':'s are'} skipped)`:''}. Each takes 1–3 minutes on this computer, so expect roughly {n}–{n*3} minutes. Every fix it finds is shown to you on its own before anything changes.</p>
   <Button className="full" disabled={backend.state!=='live'||!n} onClick={runScan}>Check all {n} paragraph{n===1?'':'s'} <ArrowUpRight/></Button>
   {backend.state!=='live'&&<p className="fineprint">Live diagnosis is unavailable: {backend.health?.model_runtime?.reason||'no Gemma runtime'}.</p>}</>;
  const done=scan.items.filter(it=>!['waiting','running'].includes(it.state)).length;
  const fixes=scan.items.filter(it=>['awaiting_review','accepted'].includes(it.state)).length;
  return <><span className="eyebrow">WHOLE DOCUMENT · {scan.running?'CHECKING':'FINISHED'}</span>
   <h2>{scan.running?<>Checking<br/>paragraphs.</>:fixes?<>{fixes} fix{fixes===1?'':'es'}<br/>to review.</>:<>No fixes<br/>found.</>}</h2>
   <p role="status">{done} of {scan.items.length} checked{scan.running?` · ${Math.floor(elapsed/60)}m ${String(elapsed%60).padStart(2,'0')}s`:''}</p>
   <p className="fineprint">Your description: “{scan.complaint}”</p>
   <ol className="scan-list">{scan.items.map(it=>{
     const p=docParagraphs.find(x=>x.paragraph_id===it.paragraph_id);
     const openable=Boolean(it.job)&&!api.isPending(it.job)&&!['waiting','running','stopped'].includes(it.state);
     return <li key={it.paragraph_id}><button className={`scan-row ${scanTone(it)}`} disabled={!openable} onClick={()=>openScanResult(it)}>
      <span>¶{it.paragraph_id}</span><span><strong>{scanText(it)}</strong><small>{it.error||(p?paragraphLabel(p).slice(0,60):'')}</small></span>{openable&&<ArrowUpRight aria-hidden="true"/>}</button></li>;})}</ol>
   {scan.running
    ?<><Button secondary onClick={stopScan}>Stop checking</Button><p className="fineprint">A paragraph already being checked still finishes on the server; it appears under My documents.</p></>
    :<><Button secondary onClick={()=>setScan(null)}>Start a new check</Button>
      {fixes>1&&<p className="fineprint">Each fix was made on your original separately. Accept one, download it, then upload that file to apply the next.</p>}</>}</>;
 };
 const liveInspector=()=>{
  if(wholeDoc)return scanPanel();
  return <>{scan&&<button className="text-button back-to-scan" onClick={backToScan}><ArrowLeft/> All paragraphs</button>}{inspectorBody()}</>;
 };

 const liveWorkspace=()=><><div className="workspace-toolbar">
   <div><FileText/><strong>{doc.is_demo_document?'Synthetic sample':'Your document'}</strong>
    <Badge tone="quiet">Original preserved</Badge>{doc.is_demo_document?<Badge tone="warning">DEMO DOCUMENT</Badge>:null}
    <small>{doc.paragraph_count} paragraphs · sha256 {doc.source_sha256?.slice(0,10)}…</small></div>
   <div className="zoom-controls"><button className="icon-button" aria-label="Zoom out" disabled={zoom<=75} onClick={()=>setZoom(z=>Math.max(75,z-10))}><Minus/></button><span>{zoom}%</span><button className="icon-button" aria-label="Zoom in" disabled={zoom>=125} onClick={()=>setZoom(z=>Math.min(125,z+10))}><Plus/></button></div></div>
  {api.isPending(job)&&<ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>}
  <div className="workspace">
   <aside className="outline-panel"><span className="eyebrow">IN THIS DOCUMENT</span>
    <button disabled={api.isPending(job)||scanBusy} className={`outline-item whole-document ${wholeDoc?'selected':''}`} aria-pressed={wholeDoc} onClick={selectWhole}>
     <span>All</span><span><strong>Whole document</strong><small>Check every paragraph</small></span></button>
    <div className="outline-scroll">{docParagraphs.map(p=><button key={p.paragraph_id} disabled={api.isPending(job)||scanBusy} className={`outline-item ${liveSelected===p.paragraph_id?'selected':''}`} aria-pressed={liveSelected===p.paragraph_id} onClick={()=>selectLive(p.paragraph_id)}>
     <span>{p.paragraph_id}</span><span><strong>{paragraphLabel(p).slice(0,38)}</strong><small>{p.style_id||'no style'}</small></span></button>)}</div>
    <SectionList report={sections} selectedId={selectedSection} disabled={api.isPending(job)} onSelect={id=>{setSelectedSection(id);if(id)setSelectedBreak(null);}}/>
    <a className="backlink" href="#upload"><Plus/> Different document</a></aside>
   <section className="canvas"><div className="canvas-label">
     <span>{canvasView==='after'
       ?(job.state==='accepted'?'AFTER · ACCEPTED REPAIR':'AFTER · PROPOSED, NOT ACCEPTED')
       :quickPreview&&canvasView==='pages'?'YOUR DOCUMENT · INSTANT PREVIEW'
       :docPages.length&&canvasView==='pages'?(afterPages.length?'BEFORE · YOUR ORIGINAL':'YOUR DOCUMENT · RENDERED BY LIBREOFFICE'):'DOCUMENT EVIDENCE'}</span>
     <span className="canvas-controls">
      {/* The page view is the document as it stands; once a job has rendered a
        * candidate, an After view shows that candidate. The text view is the same
        * paragraphs the diagnosis is given, and stays reachable because it is the
        * only view with the ids, styles and click targets the selection needs. */}
      {(quickPreview||docPages.length>0||afterPages.length>0)&&<span className="segmented small" role="group" aria-label="Document view">
        {viewOptions.map(([v,t])=><button key={v} aria-pressed={canvasView===v} onClick={()=>setDocView(v)}>{t}</button>)}</span>}
      <span>{job?`job ${job.job_id.slice(0,8)} · ${job.state}`:`document ${doc.document_id.slice(0,8)}`}</span></span></div>
    {canvasView==='after'
     ? <><PreviewPages urls={afterPages} label="Proposed version" comparison={job.visual_comparison?.pages} zoom={zoom}/>
        <p className="fineprint">{job.state==='accepted'
          ?<>This is the accepted repair. <a className="text-button inline" href={api.downloadUrl(job.job_id)} download>Download the repaired .docx</a>.</>
          :<>This is a proposal, not a change: nothing is written until you accept it. <a className="text-button inline" href="#review">Compare before and after</a>.</>}</p></>
     : quickPreview&&canvasView==='pages'
     ? <><DocxPages key={doc.document_id} source={docSource} zoom={zoom} onFailed={reason=>browserPreviewFailed(doc.document_id,reason)}/>
        <p className="fineprint">Your file as uploaded, drawn instantly by your browser. Word may lay it out slightly differently. Nothing here is a repair. To pick a paragraph, use the list on the left or switch to <button className="text-button inline" onClick={()=>setDocView('text')}>Text</button>.</p></>
     : docPages.length>0&&canvasView==='pages'
     ? <><PreviewPages urls={docPages} label="Your document" zoom={zoom}/>
        <p className="fineprint">{docPreview?.note} Nothing here is a repair: this is the file you uploaded. Switch to <button className="text-button inline" onClick={()=>setDocView('text')}>Text</button> to pick a paragraph.</p></>
     : <div className="evidence-list" style={{fontSize:`${zoom/100}rem`}}>{docParagraphs.map(p=><article key={p.paragraph_id} className={`evidence-row ${liveSelected===p.paragraph_id?'target':''}`} onClick={()=>selectLive(p.paragraph_id)}>
        <header><span>¶{p.paragraph_id}</span><small>{p.style_id||'no style'}</small></header>
        <p>{paragraphLabel(p)}</p></article>)}
       {quickPreview||docPages.length>0||!docSourceFailed
        ? null
        : docPreview===null
        ? <p className="fineprint">Your browser could not draw this file, so LibreOffice is rendering it instead… the pages appear here when it finishes.</p>
        : docPages.length===0
        ? <p className="fineprint">This document could not be rendered, so there are no pages to show: {docPreview.unavailable_reason||'the renderer is unavailable'}. Spacing, indent and alignment are still read from the file itself, so a diagnosis is unaffected.</p>
        : null}</div>}
   </section>
   <aside className="inspector">{liveInspector()}</aside></div></>;

 const liveReview=()=>['awaiting_review','accepted','rejected'].includes(job?.state)
  ?<><div className="review-heading"><div><a className="backlink" href="#workspace"><ArrowLeft/> Back to workspace</a><h1>Same words.<br/><span>{job.scope==='document'?`${proposalRows.length} break${proposalRows.length===1?'':'s'} removed.`:reviewHeadline[job.operation_kind]||'One reviewed change.'}</span></h1>
     <p>{job.diagnosis?.explanation}</p><ModeBadge job={job}/></div>
    <div className="segmented" aria-label="Comparison view">{[['side','Side by side'],['original','Original'],['proposed','Proposed']].map(([v,t])=><button key={v} aria-pressed={view===v} onClick={()=>setView(v)}>{t}</button>)}</div></div>
   <ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>
   {!previewReady&&<p className="preview-missing" id="structural-review-warning" role="note"><strong>Visual comparison is unavailable.</strong> Acceptance checks document structure only; no visual verification has occurred. Open the candidate in Word before relying on its appearance.</p>}
   <div className={`comparison ${view!=='side'?'single':''}`}>
    {view!=='proposed'&&<section><div className="compare-label">BEFORE <small>{pageCount(job.preview?.before_page_count)}</small></div><PreviewPages urls={job.preview?.before_urls||[]} label="Before"/></section>}
    {view!=='original'&&<section><div className="compare-label green">AFTER <small>{pageCount(job.preview?.after_page_count)}</small></div><PreviewPages urls={job.preview?.after_urls||[]} label="After" comparison={job.visual_comparison?.pages}/></section>}</div>
   <p className="fineprint">{job.preview?.note} Changed-pixel ratios are a review signal, not approval: a correct repair legitimately reflows later text.</p>
   <PageCountChange job={job}/>
   <BreakTargetList job={job}/>
   <KeptBreaks job={job}/>
   <SectionTargetList job={job}/>
   <InvariantList job={job}/>
   <div className="review-bar"><div><Badge>{proposalRows.length} TARGETED CHANGE{proposalRows.length===1?'':'S'}</Badge>
     <p>{proposalRows.map(opSentence).join(' · ')}</p></div>
    <div>{job.state==='accepted'
      ?<><a className="button outline" href="#results">View accepted result <ArrowUpRight/></a>
        <a className="button primary" href={api.downloadUrl(job.job_id)} download>Download repaired .docx <Download/></a></>
      :job.state==='rejected'?<Badge tone="quiet">Rejected · original kept</Badge>
      :<><Button secondary disabled={busy==='rejecting'} onClick={rejectLive}>Keep original</Button>
        <Button disabled={busy==='accepting'||structuralUnrendered} aria-describedby={!previewReady?'structural-review-warning':undefined} onClick={acceptLive}>{busy==='accepting'?'Checking integrity…':<>{structuralUnrendered?'Cannot accept without a render':previewReady?'Accept this repair':'Accept after structural review'} <Check/></>}</Button></>}</div></div></>
  :<Empty title="Nothing to review yet." onSample={openDemoDocument}>Run a diagnosis before comparing the pages.</Empty>;

 const liveResults=()=>job?.state==='accepted'
  ?<><div className="completion-icon"><Check/></div>
   <PageHeading eyebrow="THAT’S ONE LESS THING." description="The preservation invariants were re-run against the files on disk before the state changed.">Back to<br/>doing less.</PageHeading>
   <div className="result-actions top-actions"><a className="button primary" href={api.downloadUrl(job.job_id)} download>Download repaired .docx <Download/></a>
    <a className="button outline" href="#upload">Another document <Plus/></a></div>
   <div className="receipt"><div className="receipt-head"><FileText/><div><strong>repaired-{job.job_id.slice(0,8)}.docx</strong><small>job {job.job_id}</small></div><Badge>ACCEPTED</Badge></div>
    {proposalRows.map((op,i)=><div className="receipt-row" key={i}><span>{opLabel(op)}</span><strong>{opValue(op)}</strong></div>)}
    <div className="receipt-row"><span>Candidate sha256</span><strong>{job.candidate?.candidate_sha256?.slice(0,16)}…</strong></div>
    <div className="receipt-row"><span>Model</span><strong>{job.model} · {typeof job.latency_seconds==='number'?`${job.latency_seconds.toFixed(1)}s`:'—'}</strong></div></div>
   <InvariantList job={job}/>
   {!previewReady&&<p className="preview-missing"><strong>Accepted after structural review only.</strong> Visual comparison was unavailable; no visual verification occurred.</p>}
   {previewReady&&<div className="comparison results-comparison">
    <section><div className="compare-label">BEFORE <small>{pageCount(job.preview.before_page_count)}</small></div><PreviewPages urls={job.preview.before_urls||[]} label="Before"/></section>
    <section><div className="compare-label green">AFTER <small>{pageCount(job.preview.after_page_count)}</small></div><PreviewPages urls={job.preview.after_urls||[]} label="After" comparison={job.visual_comparison?.pages}/></section></div>}
   <p className="fineprint">The integrity check runs once more as the bytes are served. Your original is untouched and is always the undo path.</p>
   <a className="backlink" href="#review">Compare the pages again</a></>
  :<Empty title="A preview isn’t an approval." onSample={openDemoDocument}>Accept a reviewed candidate to download a repaired document.</Empty>;

 const liveClarify=()=><><Badge tone="warning"><AlertCircle/> ONE QUICK QUESTION</Badge>
  <PageHeading eyebrow="NO GUESSING GAMES" description="Gemma asked for a clearer complaint instead of guessing a repair.">Let’s be a little<br/>more specific.</PageHeading>
  <div className="settings-card"><span className="eyebrow">GEMMA ASKS</span><p>{job?.diagnosis?.question||'Which part of the formatting looks wrong?'}</p><small>Your request: {complaint}</small></div>
  <Button onClick={()=>{setJob(null);go('workspace');}}>Rewrite my request <ArrowUpRight/></Button></>;

 const liveHistory=()=><><div className="section-heading"><PageHeading eyebrow="THIS SERVER’S JOB TABLE" description="Held in memory by api.py. Restarting the server empties it.">Off your desk.</PageHeading><a className="button primary" href="#upload">New document <Plus/></a></div>
  <ApiErrorNote error={apiError} onDismiss={()=>setApiError(null)}/>
  {jobs.length?<div className="history-table"><div className="history-header"><span>JOB</span><span>STATE</span><span>MODEL</span><span/></div>
   {jobs.map(j=><article key={j.job_id}><div className="history-name"><FileText/><span><strong>{j.job_id.slice(0,8)}</strong><small>¶{j.paragraph_id} · {j.is_live_model_answer?'live model':'fixture'}</small></span></div>
    <Badge tone={j.state==='accepted'?'':'quiet'}>{j.state}</Badge><span>{j.model||'—'}</span>
    <button className="text-button" onClick={()=>{setJob(j);go(j.state==='accepted'?'results':'review');}}>View →</button></article>)}</div>
   :<Empty title="A beautifully empty desk." onSample={openDemoDocument}>Run a diagnosis and the job appears here.</Empty>}
  <button className="text-button" onClick={loadJobs}>Refresh</button></>;

 const livePage=()=>{
  if(route==='history')return wrap(liveHistory(),'standard-main',true);
  if(route==='upload'||(!doc&&(route==='workspace'||route==='clarify')))return wrap(liveUpload(),'upload-main',true);
  if(route==='workspace')return wrap(liveWorkspace(),'workspace-main',true,false);
  if(route==='review')return wrap(liveReview(),'review-main',true,false);
  if(route==='results')return wrap(liveResults(),'results-main',true);
  return wrap(liveClarify(),'standard-main narrow',true);
 };

 /* A real document opened against the backend is live work. If the backend becomes
  * unreachable it is held and reported, never replaced by the bundled sample. */
 const liveWork=Boolean(doc||job);
 const leaveLiveWork=()=>{abort.current?.abort();scanStop.current=true;scanAbort.current?.abort();setScan(null);setWholeDoc(false);openDocId.current=null;setDocSource(null);setDocSourceFailed(null);previewFor.current=null;setDocPreview(null);setDoc(null);setDocParagraphs([]);setLiveSelected(null);setJob(null);setApiError(null);sample();};

 let page;
 if(connected&&LIVE_ROUTES.includes(route)&&(!demoChosen||route==='upload'))page=livePage();
 else if(liveWork&&LIVE_ROUTES.includes(route))page=wrap(<OfflineHold documentName={file?.name} hasJob={Boolean(job)} error={backend.error} onRetry={checkBackend} onDemo={leaveLiveWork}/>,'standard-main narrow',true,true,false);
 else if(!demoChosen&&LIVE_ROUTES.includes(route))page=wrap(<div className="empty-panel"><AlertCircle/><h2>Choose how to continue.</h2><p>The local API is {backend.state==='checking'?'being checked':'unavailable'}. Connect it to work on a real document, or explicitly open the bundled sample with simulated analysis.</p><div className="component-row"><Button onClick={checkBackend}>Retry the connection</Button><Button secondary onClick={sample}>Open the sample demo <ArrowUpRight/></Button></div></div>,'standard-main narrow',true,true,false);
 else if(route==='home')page=<><div className="home-shell"><div className="announcement">✳ &nbsp; Less formatting. More doing absolutely nothing. &nbsp; ✳</div><section className="hero"><Header route={route}/><main id="main" ref={mainRef} tabIndex={-1} className="hero-content"><div className="hero-copy"><span className="eyebrow">THE DOCUMENT REPAIR DEPT.</span><h1>Your document.<br/>Minus the drama.</h1><p>Stubborn gaps. Strange alignment. That one heading.<br/>Let’s put everything back where it belongs.</p><div className="hero-actions"><a className="button primary" href="#upload">Upload a .docx <ArrowUpRight/></a><button className="text-button" onClick={sample}>Try a messy example ↗</button></div><small>Preview every fix. Keep your original.</small></div><div className="hero-art"><img src="assets/workshop.png" alt="A sleepy sloth in a pencil hammock above a miniature document repair workshop." fetchPriority="high"/><span className="speech">I fixed it.<br/>You nap.</span></div></main><div className="hero-bottom"><span>LESS FUSS. MORE FINISHED.</span><span>↓ MEET YOUR NEW WORK ETHIC</span></div></section></div><section className="cream-section"><Steps/></section><section className="pitch-section"><span className="eyebrow">FOR THE “WHY IS IT DOING THAT?” MOMENTS</span><h2>Good writing.<br/>Bad spacing. <span>We get it.</span></h2><div className="faults"><article><div className="fault-example gap">A heading.<i/>A very distant paragraph.</div><h3>The mysterious gap</h3><p>Find the setting behind the blank space.</p></article><article><div className="fault-example crooked">Everything lines up.<em>Except this bit.</em></div><h3>The wandering paragraph</h3><p>Bring one paragraph back into alignment.</p></article><article><div className="fault-example pagebreak">One last sentence.<b>↳ On an entirely new page.</b></div><h3>The unnecessary page</h3><p>Investigate the break before removing it.</p></article></div></section><section className="promise"><span className="eyebrow">SMALL FIXES. BIG EXHALE.</span><h2>Your words stay yours.</h2><p>Reviewable formatting changes.<br/>An original you can always come back to.</p><a className="button primary" href="#upload">Let’s fix a page <ArrowUpRight/></a></section><Footer backend={backend}/></>;
 else if(route==='upload')page=wrap(<><a className="backlink" href="#home"><ArrowLeft/> Back to the good life</a><PageHeading eyebrow="LET’S TAKE A LOOK" description="Start with our sample, or try the file-selection experience.">Drop the document.<br/>Keep your cool.</PageHeading><div className="upload-layout"><section className={`dropzone ${dragging?'dragging':''}`} onDragOver={e=>{e.preventDefault();setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false);choose(e.dataTransfer.files[0]);}}><div className="upload-emblem"><Upload/></div><h2>Your document goes here.</h2><p>Drop a .docx file, or choose one below.</p><Button onClick={()=>fileRef.current?.click()}>Choose a document <Plus/></Button><input ref={fileRef} aria-label="Choose a Word document" type="file" accept=".docx" className="visually-hidden" onChange={e=>choose(e.target.files[0])}/><small>.docx only · up to 20 MB</small>{fileError&&<p className="error" role="alert">{fileError}</p>}{file&&<div className="file-status"><div className="selected-file"><FileText/><span><strong>{file.name}</strong><small>{Math.ceil(file.size/1024)} KB · selected locally</small></span><button className="icon-button" onClick={()=>{setFile(null);if(fileRef.current)fileRef.current.value='';}} aria-label="Remove selected file"><X/></button></div><p className="notice">File selected. Your Python backend needs connecting before this file can be analysed. Nothing has been uploaded.</p><button className="text-button" onClick={sample}>Explore the sample →</button></div>}</section><aside className="upload-aside"><span className="eyebrow">JUST LOOKING AROUND?</span><div className="sample-paper"><FileText/><strong>THE MESSY<br/>REPORT</strong><i>One gap. One easy-to-see fix.</i></div><h3>Meet our slightly messy sample.</h3><p>A deliberately broken heading, ready for a test drive. No upload needed.</p><Button secondary onClick={sample}>Try the sample <ArrowUpRight/></Button></aside></div><div className="reassurance"><span><ShieldCheck/> Original preserved</span><span><Check/> You review every change</span><span><FileText/> Word output requires backend</span></div></>,'upload-main',true);
 else if(route==='workspace')page=wrap(<><div className="workspace-toolbar"><div><FileText/><strong>Research-report.docx</strong><Badge tone="quiet">Original preserved</Badge></div><div className="zoom-controls"><button className="icon-button" aria-label="Zoom out" disabled={zoom<=75} onClick={()=>setZoom(z=>Math.max(75,z-10))}><Minus/></button><span>{zoom}%</span><button className="icon-button" aria-label="Zoom in" disabled={zoom>=125} onClick={()=>setZoom(z=>Math.min(125,z+10))}><Plus/></button></div></div><div className="workspace"><aside className="outline-panel"><span className="eyebrow">IN THIS DOCUMENT</span>{paragraphs.map((p,i)=><button key={p.title} disabled={status==='running'} className={`outline-item ${selected===i?'selected':''}`} aria-pressed={selected===i} onClick={()=>selectParagraph(i)}><span>0{i+1}</span><span><strong>{p.title}</strong><small>{i===1?'Heading · spacing issue':'Report section'}</small></span>{i===1&&<i className="issue-dot"/>}</button>)}<div className="outline-note"><span>◡</span><p>One small fix.<br/>A little more peace.</p></div><a className="backlink" href="#upload"><Plus/> Different document</a></aside><section className="canvas"><div className="canvas-label"><span>{proposed?'PROPOSED VERSION':'ORIGINAL DOCUMENT'}</span><span>Page 1 of 1 · sample</span></div><Paper fixed={proposed} selected={selected} onSelect={selectParagraph} zoom={zoom}/></section><aside className="inspector">{status==='running'?<><span className="eyebrow">DEMO WALKTHROUGH</span><h2>A little patience.<br/>A lot less spacing.</h2><div className="spinner"/><p role="status">Preparing the sample comparison…</p><p className="fineprint">Timed interface simulation, not a Gemma call. Live inference on your CPU may take much longer.</p><Button secondary onClick={()=>{clearTimeout(timer.current);setStatus('idle');}}>Cancel analysis</Button></>:proposed?<><Badge><Check/> PROPOSAL READY · DEMO</Badge><h2>A little<br/>less space.</h2><p>The sample heading uses 48 pt above it. Its three same-style peers use 12 pt.</p><div className="property-table"><div><span>Space above</span><strong><s>48 pt</s> → 12 pt</strong></div><div><span>Target</span><strong>Methodology</strong></div></div><ul className="checks"><li><Check/> Sample text preserved</li><li><Check/> One paragraph targeted</li><li><Check/> Original stays available</li></ul><p className="fineprint">Illustrative evidence for the fixed sample, not a validation of your uploaded file.</p><a className="button primary full" href="#review">Review the change <ArrowUpRight/></a><button className="text-button" onClick={reject}>Keep the original</button></>:<><span className="eyebrow">TELL US WHAT’S OFF</span><h2>What the<br/>document?</h2>{status==='rejected'&&<Badge tone="quiet">Original kept</Badge>}<label className="field-label" htmlFor="complaint">What looks wrong?</label><textarea id="complaint" rows={4} value={complaint} onChange={e=>setComplaint(e.target.value)} placeholder="This heading is miles from the paragraph above it."/><div className="chips">{['Too much space','Wrong alignment','Page break'].map(t=><button key={t} onClick={()=>setComplaint(t)}>{t}</button>)}</div><div className="selection-detail"><span className="eyebrow">SELECTED PARAGRAPH</span><strong>{paragraphs[selected].title}</strong><small>{selected===1?'Space above: 48 pt · direct formatting':'No sample fault in this paragraph'}</small></div><Button className="full" onClick={diagnose}>Preview a repair <ArrowUpRight/></Button><p className="fineprint">The demo supports the Methodology spacing example. Other requests show a clarification state.</p></>}</aside></div></>,'workspace-main',true,false);
 else if(route==='review')page=wrap(proposed?<><div className="review-heading"><div><a className="backlink" href="#workspace"><ArrowLeft/> Back to workspace</a><h1>Same words.<br/><span>Better breathing room.</span></h1></div><div className="segmented" aria-label="Comparison view">{[['side','Side by side'],['original','Original'],['proposed','Proposed']].map(([v,t])=><button key={v} aria-pressed={view===v} onClick={()=>setView(v)}>{t}</button>)}</div></div><div className={`comparison ${view!=='side'?'single':''}`}>{view!=='proposed'&&<section><div className="compare-label">BEFORE <small>48 pt above heading</small></div><Paper/></section>}{view!=='original'&&<section><div className="compare-label green">AFTER <small>12 pt above heading</small></div><Paper fixed/></section>}</div><div className="review-bar"><div><Badge>1 TARGETED CHANGE</Badge><p>Methodology · Space above <s>48 pt</s> → <strong>12 pt</strong></p></div><div>{status==='accepted'?<a className="button primary" href="#results">View accepted result <ArrowUpRight/></a>:<><Button secondary onClick={reject}>Keep original</Button><Button onClick={accept}>Accept sample repair <Check/></Button></>}</div></div></>:<Empty title="Nothing to review yet." onSample={sample}>Preview a repair before comparing the pages.</Empty>,'review-main',true,false);
 else if(route==='results')page=wrap(status==='accepted'?<><div className="completion-icon"><Check/></div><PageHeading eyebrow="THAT’S ONE LESS THING." description="The sample repair is accepted. Back to the important business of doing less.">Back to<br/>doing less.</PageHeading><div className="receipt"><div className="receipt-head"><FileText/><div><strong>Research-report.docx</strong><small>Demonstration complete</small></div><Badge>REVIEWED</Badge></div><div className="receipt-row"><span>Space above Methodology</span><strong><s>48 pt</s> → 12 pt</strong></div><div className="receipt-row"><span>Sample paragraphs changed</span><strong>1</strong></div><div className="receipt-row"><span>Actual DOCX generated</span><strong>No · frontend demo</strong></div></div><div className="result-actions"><Button onClick={download}>Download demo report <Download/></Button><a className="button outline" href="#upload">Another document <Plus/></a></div><p className="fineprint">This download is a JSON change report. Repaired Word downloads require the Python backend.</p><a className="backlink" href="#review">Compare the sample again</a></>:<Empty title="A preview isn’t an approval." onSample={sample}>Review and accept a sample repair to see its result.</Empty>,'results-main',true);
 else if(route==='history')page=wrap(<><div className="section-heading"><PageHeading eyebrow="YOUR LITTLE PAPER TRAIL" description="Sample repairs you’ve accepted in this browser.">Off your desk.</PageHeading><a className="button primary" href="#upload">New document <Plus/></a></div>{history.length?<><div className="history-table"><div className="history-header"><span>DOCUMENT</span><span>REVIEWED</span><span>STATUS</span><span/></div>{history.map(item=><article key={item.id}><div className="history-name"><FileText/><span><strong>Research-report.docx</strong><small>1 spacing adjustment · sample</small></span></div><time dateTime={item.date}>{new Date(item.date).toLocaleDateString(undefined,{month:'short',day:'numeric'})}</time><Badge>Accepted</Badge><button className="text-button" onClick={()=>{setSelected(1);setStatus('accepted');go('results');}}>View →</button></article>)}</div><button className="text-button danger" onClick={()=>{storeHistory([]);notify('Demo history cleared.');}}>Clear demo history</button></>:<Empty title="A beautifully empty desk." onSample={sample}>Accept a sample repair and we’ll put the receipt here.</Empty>}</>,'standard-main',true);
 else if(route==='clarify')page=wrap(<><Badge tone="warning"><AlertCircle/> ONE QUICK QUESTION</Badge><PageHeading eyebrow="NO GUESSING GAMES" description="This demo models the oversized gap above Methodology. It won’t invent a repair for another request.">Let’s be a little<br/>more specific.</PageHeading><div className="settings-card"><span className="eyebrow">YOUR REQUEST</span><p>{complaint}</p><small>Selected: {paragraphs[selected].title}</small></div><Button onClick={sample}>Show the spacing example <ArrowUpRight/></Button><a className="backlink" href="#workspace">Change my request</a></>,'standard-main narrow',true);
 else if(route==='settings')page=wrap(<><PageHeading eyebrow="MAKE YOURSELF COMFORTABLE">A few preferences.</PageHeading><div className="settings-card"><h2>Appearance</h2><label className="setting-row"><span><strong>Gentle animations</strong><small>Slow movement around the workshop. System reduced-motion settings always take precedence.</small></span><input type="checkbox" checked={motion} onChange={e=>setMotion(e.target.checked)}/></label></div><div className="settings-card"><h2>Connection</h2><div className="setting-row"><span><strong>{connected?`Local API · ${backend.state==="live"?"live diagnosis available":"reachable, no model"}`:"Frontend demo"}</strong><small>{connected?`${backend.health?.model_runtime?.configured_model||"no model"} · renderer ${backend.health?.renderer?.available?"available":"unavailable"}`:"Gemma, Word rendering and repaired downloads are not connected."}</small></span><Badge tone={connected?"":"quiet"}>{connected?backend.state.toUpperCase():"LOCAL"}</Badge></div><p>Start it with <code>python api.py --cors http://localhost:5173</code>, or run the Vite dev server, which proxies <code>/api</code> to 127.0.0.1:8000. The demo never silently switches to live processing, and the live mode never substitutes a canned answer.</p><Button secondary onClick={checkBackend}>Re-check the connection</Button></div><div className="settings-card"><h2>Your browser data</h2><p>Only sample acceptance IDs and timestamps are saved locally. Selected files are held in memory until removed or the page reloads.</p><Button secondary onClick={()=>{storeHistory([]);notify('Demo history cleared.');}}>Clear demo history</Button></div></>,'standard-main narrow');
 else if(route==='guide')page=wrap(<><PageHeading eyebrow="A VERY SMALL AMOUNT OF EFFORT">You point.<br/>We investigate.</PageHeading><Steps/><div className="guide-grid"><article><h2>01 / Bring your words.</h2><p>The connected product inspects paragraph styles and renders your Word file. Every proposed version stays separate from the original.</p></article><article><h2>02 / Be human about it.</h2><p>“Why is this heading floating?” is a good place to start. Gemma diagnoses; the deterministic engine decides which edits are allowed.</p></article><article><h2>03 / Have the final say.</h2><p>Compare the pages and review the evidence. Accept the change or keep the original. A completed analysis is never an automatic approval.</p></article><article><h2>A small, important note.</h2><p>This package demonstrates the interface using a fixed sample. Live model analysis, accurate renders and repaired DOCX output need your Python backend.</p></article></div><Button onClick={sample}>Take the sample for a spin <ArrowUpRight/></Button></>);
 else if(route==='privacy')page=wrap(<><PageHeading eyebrow="YOUR WORDS. YOUR CALL.">No mystery<br/>behind the paper.</PageHeading><div className="prose"><h2>In this frontend demo</h2><p>Selected files remain in browser memory. They are not uploaded, parsed or repaired. Reloading clears the selection. The illustrative sample is bundled with the interface.</p><h2>What is stored</h2><p>Accepting the sample saves an ID and timestamp in local browser storage. Clear it in Settings. This is not an account or a cloud document library.</p><h2>When the backend is connected</h2><p>Your application must explain where documents and model requests are processed, how long files are retained and how deletion works. This frontend makes no claims about renderer sandboxing or cloud privacy.</p><h2>Assets and connections</h2><p>The workshop artwork and the Rubik display font (SIL Open Font License) are bundled and served from this origin. No analytics, font CDN or other third-party request is made. The only network calls are to the local API on this machine.</p></div><a className="button outline" href="#settings">Manage demo data <ArrowUpRight/></a></>,'standard-main narrow');
 else if(route==='design')page=wrap(<><PageHeading eyebrow="THE WORKSHOP DESIGN KIT" description="The landing page celebrates doing less. The workspace gives your document room to breathe.">Playful outside.<br/>Precise inside.</PageHeading><div className="swatches">{[['Apricot','#FF8739'],['Forest','#143B2F'],['Cream','#FFF7E9'],['Ink','#13221D'],['Mint','#DDE9CF'],['Proof red','#B8422D']].map(([name,color])=><div key={name}><span style={{background:color}}/><strong>{name}</strong><code>{color}</code></div>)}</div><div className="guide-grid"><article><span className="eyebrow">DISPLAY TYPE</span><h2 className="type-demo">A little less busy.</h2><p>Heavy, tight sans-serif for personality. Clear serif typography inside the document preview.</p></article><article><span className="eyebrow">BUTTONS & STATES</span><div className="component-row"><Button onClick={sample}>Primary <ArrowUpRight/></Button><Button secondary onClick={sample}>Secondary</Button><Button disabled>Working…</Button></div></article><article><span className="eyebrow">STATUS LANGUAGE</span><div className="component-row"><Badge>Reviewed</Badge><Badge tone="quiet">Original preserved</Badge><Badge tone="warning">Needs input</Badge></div><p>Colour accompanies words. Evidence supports every check.</p></article><article><span className="eyebrow">MOTION</span><h2>Easy does it.</h2><p>A slow floating illustration. Quick control feedback. Reduced-motion settings remove decorative animation.</p></article></div></>);
 else page=wrap(<Empty title="This page wandered off." onSample={sample}>Let’s head back to the workshop.</Empty>);
 return <div className={motion?'app':'app no-motion'}><a className="skip-link" href="#main" onClick={e=>{e.preventDefault();mainRef.current?.focus();}}>Skip to content</a>{page}<div className={`toast ${notice?'visible':''}`} role="status" aria-live="polite">{notice}</div></div>;
}
