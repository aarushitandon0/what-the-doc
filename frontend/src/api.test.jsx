import React from 'react';
import {describe,it,expect,vi,beforeEach,afterEach} from 'vitest';
import {render,screen,fireEvent,act,waitFor,within} from '@testing-library/react';
import App from './App';
import * as api from './api';

/* docx-preview draws into the DOM with layout jsdom does not have. This stand-in keeps
 * its contract: it draws a page for a real .docx (a ZIP, so it starts with PK) and
 * rejects anything else, which is what sends the app to the LibreOffice fallback. It
 * also plants a script link, which the app must disarm. */
vi.mock('docx-preview',()=>({renderAsync:vi.fn(async(source,el)=>{
 // jsdom's Blob has no text(), so the bytes are read the older way.
 const text=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(r.error);r.readAsText(source);});
 if(!text.startsWith('PK'))throw new Error('not a zip file');
 const page=document.createElement('section');page.className='docx';page.textContent='Drawn: '+text.slice(3);
 const link=document.createElement('a');link.setAttribute('href','javascript:alert(1)');link.textContent='planted link';page.append(link);
 el.append(page);
})}));

/* A router over fetch: every response here is shaped like API.md, so a change in the
 * contract shows up as a failing test rather than as a surprise in the browser. */
const HEALTH_LIVE={service:'what-the-doc',model_runtime:{configured_model:'gemma4:e2b',available:true,model_installed:true,reason:null},live_diagnosis_available:true,renderer:{available:true,binary:'soffice.exe'},fixture_mode_allowed:false};
const DOCUMENT={document_id:'9f1c3a7e4b2d08516c7a9e0f33b1d245',source_sha256:'ca278f',paragraph_count:2,has_styles_part:true,size_bytes:10244,origin:'synthetic_demo_fixture',is_demo_document:true};
const PARAGRAPHS={document_id:DOCUMENT.document_id,paragraphs:[
 {paragraph_id:1,text:'Methodology',style_id:'Heading1',style_chain:['Heading1'],effective_properties:{space_before_pt:{value:48,origin:'direct',overridden_by:[]},alignment:{value:null,origin:'application_default',overridden_by:[]}},resolution_caveats:[]},
 {paragraph_id:2,text:'Findings',style_id:'Heading1',style_chain:['Heading1'],effective_properties:{space_before_pt:{value:12,origin:'style:Heading1',overridden_by:[]}},resolution_caveats:[]}]};
/* Two page breaks in one paragraph, plus one the engine refuses, exactly as
 * GET /breaks reports them. The ineligible one is in the fixture on purpose: the
 * selector has to show it, disabled, with the server's own reason. */
const BREAKS={source_sha256:'ca278f',break_count:3,removable_count:2,last_rendered_page_break_count:1,removable_break_types:['page','column'],
 breaks:[
  {kind:'explicit_break',break_type:'page',break_type_raw:'page',paragraph_id:1,style_id:'Heading1',run_index:1,break_index_in_run:0,ordinal_in_paragraph:1,edit_eligible:true,ineligible_reason:null,text_before:'closed for the quarter.',text_after:'Then the balances',run_has_properties:false,runs_in_paragraph:3,target_id:'brk-1111111111111111'},
  {kind:'explicit_break',break_type:'page',break_type_raw:'page',paragraph_id:1,style_id:'Heading1',run_index:2,break_index_in_run:0,ordinal_in_paragraph:2,edit_eligible:true,ineligible_reason:null,text_before:'Then the balances were confirmed.',text_after:'Finally the register',run_has_properties:false,runs_in_paragraph:3,target_id:'brk-2222222222222222'},
  {kind:'explicit_break',break_type:'page',break_type_raw:'page',paragraph_id:1,style_id:'Heading1',run_index:3,break_index_in_run:0,ordinal_in_paragraph:3,edit_eligible:false,ineligible_reason:'The break sits inside <w:hyperlink> rather than directly in a top-level body paragraph, so this version will not edit it.',text_before:'a link',text_after:'',run_has_properties:false,runs_in_paragraph:4,target_id:'brk-3333333333333333'},
  {kind:'last_rendered_page_break',break_type:null,paragraph_id:1,style_id:'Heading1',run_index:1,target_id:null,edit_eligible:false,ineligible_reason:'w:lastRenderedPageBreak records where the application that last paginated this document placed a page break. It is not a user-inserted break, so removing it would repair nothing.',text_before:'',text_after:''}],
 warning:'Identification is not a recommendation to remove.'};
/* Two sections. Section 0 is the first one and is never convertible; section 1 is
   the final body sectPr, starts nextPage, and is the single supported target. */
const SECTIONS={source_sha256:'ca278f',section_count:2,has_final_body_section_properties:true,editable:true,
 editable_target_ids:['sec-1111111111111111'],
 supported_start_changes:[{from:'nextPage',to:'continuous'}],
 read_only_reason:'Sections are inspected in full and exactly one mutation is offered.',
 sections:[
  {section_index:0,defined_in:'paragraph_properties',defining_paragraph_id:1,governs:'the section that ends with paragraph 1',start_type:'nextPage',start_type_raw:'nextPage',start_type_is_default:false,is_continuous:false,starts_new_column:false,columns:{declared:false,count:1},target_id:'sec-0000000000000000',editable:false,edit_eligible:false,supported_start_changes:[],ineligible_reason:'This is the first section of the document. A section start describes how a section follows the previous one, and there is no previous section here, so changing it would repair nothing.',differing_page_properties:null,is_final_section:false},
  {section_index:1,defined_in:'body',defining_paragraph_id:null,governs:'the final section of the document',start_type:'nextPage',start_type_raw:null,start_type_is_default:true,is_continuous:false,starts_new_column:false,columns:{declared:false,count:1},target_id:'sec-1111111111111111',editable:true,edit_eligible:true,supported_start_changes:['continuous'],ineligible_reason:null,differing_page_properties:[],is_final_section:true}],
 warning:'Paragraph ranges are body-paragraph ids, not page numbers.'};
/* A reviewable job whose approved change is one explicit break removal. */
const BREAK_JOB_ID='52abcdef0123';
const BREAK_REVIEWABLE={job_id:BREAK_JOB_ID,document_id:'9f1c3a7e4b2d08516c7a9e0f33b1d245',state:'awaiting_review',mode:'live',is_live_model_answer:true,paragraph_id:1,accepted:false,error_code:null,
 operation_kind:'explicit_break_removal',plan_schema_version:2,break_target_id:'brk-2222222222222222',rendering_required:true,rendering_satisfied:true,
 diagnosis:{status:'proposed',explanation:'The second page break leaves a nearly empty page.',evidence:[{paragraph_id:1,target_id:'brk-2222222222222222'}],question:null},
 proposal:{source_sha256:'ca278f',schema_version:2,operations:[{operation:'remove_explicit_break',target_id:'brk-2222222222222222',expected_break_type:'page'}]},
 break_targets:[{target_id:'brk-2222222222222222',break_type:'page',paragraph_id:1,ordinal_in_paragraph:2,text_before:'Then the balances were confirmed.',text_after:'Finally the register'}],
 preview:{rendered:true,before_page_count:6,after_page_count:5,before_urls:['/api/jobs/52abcdef0123/preview/before/0'],after_urls:['/api/jobs/52abcdef0123/preview/after/0'],renderer_available:true,note:'Rendered by LibreOffice.'},
 visual_comparison:{before_pages:6,after_pages:5,page_count_delta:-1,pages:[{page:1,status:'compared',changed_ratio:0.31}]},
 candidate:{candidate_sha256:'9ab1',reused:false,accept_requires_candidate_sha256:'9ab1',download_available:false},
 invariants:{only_approved_breaks_removed:{passed:true,detail:'breaks differ by exactly the 1 approved removal'}},invariant_failures:[],model:'gemma4:e2b',latency_seconds:41.2,decision:null};

const REVIEWABLE={job_id:'41abcdef0123',document_id:DOCUMENT.document_id,state:'awaiting_review',mode:'live',is_live_model_answer:true,paragraph_id:1,accepted:false,error_code:null,
 diagnosis:{status:'proposed',explanation:'Direct 48pt where peers inherit 12pt.',evidence:[{paragraph_id:1,field:'space_before_pt'}],question:null},
 proposal:{source_sha256:'ca278f',operations:[{paragraph_id:1,field:'space_before_pt',value:12}]},
 preview:{rendered:true,before_page_count:1,after_page_count:1,before_urls:['/api/jobs/41abcdef0123/preview/before/0'],after_urls:['/api/jobs/41abcdef0123/preview/after/0'],renderer_available:true,note:'Rendered by LibreOffice.'},
 visual_comparison:{pages:[{page:1,status:'compared',changed_ratio:0.26}]},
 candidate:{candidate_sha256:'7d3e',reused:false,accept_requires_candidate_sha256:'7d3e',download_available:false},
 invariants:{original_unmodified:{passed:true,detail:'sha256 unchanged'}},invariant_failures:[],model:'gemma4:e2b',latency_seconds:73.6,decision:null};

/* The uploaded document rendered to pages, shaped like API.md. This is the document
 * as it stands - not a comparison and not a repair. */
const DOCUMENT_PREVIEW={document_id:DOCUMENT.document_id,rendered:true,page_count:2,
 urls:[`/api/documents/${DOCUMENT.document_id}/preview/0`,`/api/documents/${DOCUMENT.document_id}/preview/1`],
 renderer_available:true,unavailable_reason:null,
 note:'The document as uploaded, rendered by LibreOffice. It approximates Word; it is not Word.'};

function mockApi(overrides={}){
 const calls=[];
 const routes={
  'GET /api/health':()=>HEALTH_LIVE,
  'POST /api/documents':()=>({...DOCUMENT,origin:'upload',is_demo_document:false}),
  'POST /api/demo/document':()=>DOCUMENT,
  [`GET /api/documents/${DOCUMENT.document_id}/paragraphs`]:()=>PARAGRAPHS,
  [`GET /api/documents/${DOCUMENT.document_id}/breaks`]:()=>BREAKS,
  [`GET /api/documents/${DOCUMENT.document_id}/sections`]:()=>SECTIONS,
  [`GET /api/documents/${DOCUMENT.document_id}/preview`]:()=>DOCUMENT_PREVIEW,
  'POST /api/jobs':()=>({...REVIEWABLE,state:'queued',diagnosis:null,proposal:null,preview:null}),
  [`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>REVIEWABLE,
  [`POST /api/jobs/${REVIEWABLE.job_id}/accept`]:()=>({...REVIEWABLE,state:'accepted',accepted:true,decision:'accepted'}),
  [`POST /api/jobs/${REVIEWABLE.job_id}/reject`]:()=>({...REVIEWABLE,state:'rejected',decision:'rejected'}),
  'GET /api/jobs':()=>({jobs:[REVIEWABLE]}),
  ...overrides,
 };
 const fetchMock=vi.fn(async(url,options={})=>{
  const key=`${options.method||'GET'} ${url}`;
  calls.push({key,options});
  const handler=routes[key];
  if(!handler)return {ok:false,status:404,json:async()=>({error_code:'not_found',detail:key})};
  const result=handler(options);
  if(result?.__status)return {ok:false,status:result.__status,json:async()=>result.body};
  if(result instanceof Blob)return {ok:true,status:200,blob:async()=>result};
  return {ok:true,status:200,json:async()=>result};
 });
 vi.stubGlobal('fetch',fetchMock);
 return {calls,fetchMock};
}

afterEach(()=>{vi.unstubAllGlobals();});

/* Nothing is pre-selected any more: each test picks the paragraph it means, exactly
 * as a person has to. */
async function pick(name=/Methodology/){
 const outline=await waitFor(()=>{const el=document.querySelector('.outline-scroll');if(!el)throw new Error('no outline yet');return el;});
 await act(async()=>{fireEvent.click(within(outline).getByRole('button',{name}));});
}

describe('api client',()=>{
 beforeEach(()=>{mockApi();});
 it('uploads the file under the field name the backend expects',async()=>{
  const file=new File(['x'],'report.docx');
  await api.uploadDocument(file);
  const [,options]=fetch.mock.calls.at(-1);
  expect(options.method).toBe('POST');
  expect(options.body.get('file')).toBe(file);
 });
 it('refuses to accept without the candidate hash the job demands',()=>{
  expect(()=>api.acceptJob('41abcdef0123',undefined)).toThrow(/candidate hash/i);
 });
 it('sends the reviewed candidate hash on accept',async()=>{
  await api.acceptJob(REVIEWABLE.job_id,'7d3e');
  const [url,options]=fetch.mock.calls.at(-1);
  expect(url).toBe('/api/jobs/41abcdef0123/accept');
  expect(JSON.parse(options.body)).toEqual({candidate_sha256:'7d3e'});
 });
 it('surfaces the backend error_code rather than the prose',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({error_code:'model_unavailable',detail:'no runtime'})})));
  await expect(api.createJob({documentId:'d',paragraphId:1,complaint:'x'})).rejects.toMatchObject({code:'model_unavailable',status:503});
 });
 it('reports an unreachable backend as its own code',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('Failed to fetch');}));
  await expect(api.health()).rejects.toMatchObject({code:'backend_unreachable'});
 });
});

describe('connected workspace',()=>{
 it('walks upload, diagnosis, review, acceptance and download against the API',async()=>{
  mockApi();
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();

  // paragraphs come from the backend, and an unset property is never shown as 0
  expect((await screen.findAllByText('Methodology')).length).toBeGreaterThan(0);
  expect(screen.getByText('not set (Word default)')).toBeInTheDocument();
  expect(screen.getByText(/DEMO DOCUMENT/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));
  const started=await waitFor(()=>fetch.mock.calls.find(([url,o])=>url==='/api/jobs'&&o?.method==='POST'));
  expect(JSON.parse(started[1].body)).toMatchObject({document_id:DOCUMENT.document_id,paragraph_id:1,render:true,expected_source_sha256:'ca278f'});

  await screen.findByText(/Direct 48pt where peers inherit 12pt/);
  act(()=>{window.location.hash='review';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  expect(await screen.findByAltText('Before page 1')).toHaveAttribute('src','/api/jobs/41abcdef0123/preview/before/0');
  expect(screen.getByAltText('After page 1')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:/Accept this repair/}));
  const accepted=await waitFor(()=>fetch.mock.calls.find(([url])=>url.endsWith('/accept')));
  expect(JSON.parse(accepted[1].body)).toEqual({candidate_sha256:'7d3e'});
  expect(await screen.findByRole('link',{name:/Download repaired .docx/})).toHaveAttribute('href','/api/jobs/41abcdef0123/download');
 },20000);

 it('offers no download before a human accepts',async()=>{
  mockApi();
  render(<App/>);
  act(()=>{window.location.hash='results';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  expect(await screen.findByText('A preview isn’t an approval.')).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/Download/})).not.toBeInTheDocument();
 });

 it('makes structural-only acceptance explicit when visual comparison is unavailable',async()=>{
  const structural={...REVIEWABLE,preview:{rendered:false,before_urls:[],after_urls:[],note:'Renderer unavailable.'},visual_comparison:null};
  mockApi({
   [`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>structural,
   [`POST /api/jobs/${REVIEWABLE.job_id}/accept`]:()=>({...structural,state:'accepted',accepted:true}),
  });
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  fireEvent.click(await screen.findByRole('button',{name:/Diagnose with Gemma/}));
  fireEvent.click(await screen.findByRole('link',{name:/Review the change/}));
  expect(await screen.findByText(/Acceptance checks document structure only/)).toBeInTheDocument();
  const accept=screen.getByRole('button',{name:/Accept after structural review/});
  expect(accept).toHaveAttribute('aria-describedby','structural-review-warning');
  expect(screen.queryByRole('link',{name:/Download/})).not.toBeInTheDocument();
  fireEvent.click(accept);
  expect(await screen.findByText(/no visual verification occurred/)).toBeInTheDocument();
  expect(screen.getByRole('link',{name:/Download repaired/})).toBeInTheDocument();
 },20000);

 it('shows the runtime reason instead of a substitute when no model is installed',async()=>{
  mockApi({'GET /api/health':()=>({...HEALTH_LIVE,live_diagnosis_available:false,model_runtime:{...HEALTH_LIVE.model_runtime,available:false,reason:'gemma4:e2b is not installed'}})});
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  expect(await screen.findByText(/gemma4:e2b is not installed/)).toBeInTheDocument();
 });

 it('requires an explicit choice before opening the sample while offline',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('Failed to fetch');}));
  render(<App/>);
  act(()=>{window.location.hash='workspace';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  expect(await screen.findByText(/BACKEND OFFLINE/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Original sample document')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Open the sample demo/}));
  expect(await screen.findByLabelText('Original sample document')).toBeInTheDocument();
 });

 /* A backend that drops out mid-session must not quietly swap the user's real
  * document for the bundled sample: the two would then be indistinguishable on
  * screen, and a sample proposal could be read as a result for the real file. */
 it('holds an opened real document instead of silently replacing it with the sample',async()=>{
  let reachable=true;
  const {fetchMock}=mockApi();
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
   if(!reachable)throw new TypeError('Failed to fetch');
   return fetchMock(url,options);
  }));
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  fireEvent.change(await screen.findByLabelText('Choose a Word document'),{target:{files:[new File(['x'],'my-real-report.docx')]}});
  expect((await screen.findAllByText('Methodology')).length).toBeGreaterThan(0);

  // the backend dies, and the user re-checks the connection
  reachable=false;
  act(()=>{window.location.hash='settings';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  fireEvent.click(await screen.findByRole('button',{name:/Re-check the connection/}));
  await screen.findAllByText(/Frontend demo/);
  act(()=>{window.location.hash='workspace';window.dispatchEvent(new HashChangeEvent('hashchange'));});

  expect(await screen.findByText(/BACKEND OFFLINE/)).toBeInTheDocument();
  expect(await screen.findByText(/Your document is still open/)).toBeInTheDocument();
  // the bundled sample must not have taken its place
  expect(screen.queryByLabelText('Original sample document')).not.toBeInTheDocument();
  expect(screen.queryByText(/INTERACTIVE DEMO/)).not.toBeInTheDocument();
  expect(screen.queryByText('Research-report.docx')).not.toBeInTheDocument();

  // ...and reaching the sample is an explicit, separate choice
  fireEvent.click(screen.getByRole('button',{name:/Try the demo instead/}));
  expect(await screen.findByLabelText('Original sample document')).toBeInTheDocument();
 },20000);
});

/* The explicit-break slice in the interface. Every response below is shaped like
 * API.md, so these establish what the UI does with what the server actually sends,
 * and nothing about model behaviour or real rendering. */
describe('explicit breaks',()=>{
 async function openWorkspace(overrides={}){
  const mock=mockApi(overrides);
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  await screen.findByText(/EXPLICIT BREAKS IN THIS PARAGRAPH/);
  return mock;
 }

 it('lists every break in the paragraph so two in one paragraph are distinguishable',async()=>{
  await openWorkspace();
  // Same paragraph, same type: the ordinal and the nearby text are what tell them apart.
  expect(screen.getByRole('button',{name:/Page break 1 in ¶1/})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/Page break 2 in ¶1/})).toBeInTheDocument();
  expect(screen.getByText(/closed for the quarter\./)).toBeInTheDocument();
  expect(screen.getByText(/Then the balances were confirmed\./)).toBeInTheDocument();
  // No two rows in one paragraph may read the same, including the one that cannot
  // be edited: the ordinal runs across every break of that type in the paragraph.
  const labels=screen.getAllByRole('button',{name:/break \d+ in ¶1/}).map(b=>b.textContent.split('PAGE')[0].split('NOT')[0].trim());
  expect(new Set(labels).size).toBe(labels.length);
 });

 it('shows an unsupported break as unselectable with the reason the server gave',async()=>{
  await openWorkspace();
  const blocked=screen.getAllByRole('button',{name:/NOT REMOVABLE/});
  expect(blocked.length).toBe(2);
  blocked.forEach(button=>expect(button).toBeDisabled());
  expect(screen.getByText(/sits inside <w:hyperlink>/)).toBeInTheDocument();
  expect(screen.getByText(/not a user-inserted break/)).toBeInTheDocument();
 });

 it('names a break inside a table instead of counting it as one elsewhere',async()=>{
  const inTable={kind:'explicit_break',break_type:'page',break_type_raw:'page',paragraph_id:null,container:'tbl',style_id:null,run_index:null,break_index_in_run:null,ordinal_in_paragraph:null,edit_eligible:false,ineligible_reason:'The break sits inside <w:tbl>, outside the top-level body paragraphs this version edits, so it is listed and will not be removed.',text_before:'',text_after:'In a table cell.',run_has_properties:false,runs_in_paragraph:null,target_id:null};
  await openWorkspace({[`GET /api/documents/${DOCUMENT.document_id}/breaks`]:()=>({...BREAKS,break_count:4,breaks:[...BREAKS.breaks,inTable]})});
  expect(screen.getByText(/1 break inside a table: listed, not removable/)).toBeInTheDocument();
  expect(screen.queryByText(/more break.* elsewhere/)).not.toBeInTheDocument();
  // It is not a button anywhere: there is no paragraph to select it from.
  expect(screen.getAllByRole('button',{name:/NOT REMOVABLE/}).length).toBe(2);
 });

 it('sends no break target when the person has not chosen one',async()=>{
  await openWorkspace();
  // Two removable breaks and none chosen: the interface says so rather than picking
  // one, and the request carries no selection, so nothing downstream can claim the
  // person pointed at a particular break.
  expect(screen.getByText(/Pick the one you mean/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));
  const posted=await waitFor(()=>fetch.mock.calls.find(([url,o])=>url==='/api/jobs'&&o?.method==='POST'));
  expect(JSON.parse(posted[1].body).break_target_id).toBeUndefined();
 },20000);

 it('sends exactly the break the person chose',async()=>{
  await openWorkspace();
  fireEvent.click(screen.getByRole('button',{name:/Page break 2 in ¶1/}));
  expect(screen.getByRole('button',{name:/Page break 2 in ¶1/})).toHaveAttribute('aria-pressed','true');
  expect(screen.getByRole('button',{name:/Page break 1 in ¶1/})).toHaveAttribute('aria-pressed','false');
  // Choosing a break removes the ambiguity note, because there is no longer any.
  expect(screen.queryByText(/Pick the one you mean/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));
  const posted=await waitFor(()=>fetch.mock.calls.find(([url,o])=>url==='/api/jobs'&&o?.method==='POST'));
  expect(JSON.parse(posted[1].body).break_target_id).toBe('brk-2222222222222222');
 },20000);

 it('shows which break is going, the page counts and an explicit approval',async()=>{
  mockApi({
   'POST /api/jobs':()=>({...BREAK_REVIEWABLE,state:'queued',diagnosis:null,proposal:null,preview:null,break_targets:null}),
   [`GET /api/jobs/${BREAK_JOB_ID}`]:()=>BREAK_REVIEWABLE,
   [`POST /api/jobs/${BREAK_JOB_ID}/accept`]:()=>({...BREAK_REVIEWABLE,state:'accepted',accepted:true}),
  });
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  fireEvent.click(await screen.findByRole('button',{name:/Diagnose with Gemma/}));
  fireEvent.click(await screen.findByRole('link',{name:/Review the change/}));

  // The inspector also lists the target, so wait for something only the review
  // route renders before reading the rest of that page.
  await screen.findByText(/Changed-pixel ratios are a review signal/);
  expect(screen.getAllByText(/THE BREAK BEING REMOVED/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/Only this/).length).toBeGreaterThan(0);
  // The page count is reported, and the wording does not promise a reduction.
  expect(screen.getByText(/6 pages → 5 pages/)).toBeInTheDocument();
  expect(screen.getByText(/not an approval/)).toBeInTheDocument();
  // The heading names this kind of change, not the spacing sample's line.
  expect(screen.getByText('One break removed.')).toBeInTheDocument();
  expect(screen.queryByText('Better breathing room.')).not.toBeInTheDocument();
  expect(screen.getAllByText(/remove one page break/).length).toBeGreaterThan(0);
  // Reaching review is not approval: acceptance is a separate press.
  expect(screen.queryByRole('link',{name:/Download repaired/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Accept this repair/}));
  const accepted=await waitFor(()=>fetch.mock.calls.find(([url])=>url.endsWith('/accept')));
  expect(JSON.parse(accepted[1].body)).toEqual({candidate_sha256:'9ab1'});
 },20000);

 it('states a reflow without a page-count change rather than claiming a page was saved',async()=>{
  const flat={...BREAK_REVIEWABLE,
   visual_comparison:{before_pages:2,after_pages:2,page_count_delta:0,pages:[{page:1,status:'compared',changed_ratio:0.0065}]},
   preview:{...BREAK_REVIEWABLE.preview,before_page_count:2,after_page_count:2},
   break_targets:[{...BREAK_REVIEWABLE.break_targets[0],break_type:'column'}],
   proposal:{source_sha256:'ca278f',schema_version:2,operations:[{operation:'remove_explicit_break',target_id:'brk-2222222222222222',expected_break_type:'column'}]}};
  mockApi({
   'POST /api/jobs':()=>({...flat,state:'queued',diagnosis:null,proposal:null,preview:null,break_targets:null}),
   [`GET /api/jobs/${BREAK_JOB_ID}`]:()=>flat,
  });
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  fireEvent.click(await screen.findByRole('button',{name:/Diagnose with Gemma/}));
  fireEvent.click(await screen.findByRole('link',{name:/Review the change/}));
  expect(await screen.findByText(/The page count is unchanged/)).toBeInTheDocument();
  expect(screen.getByText(/does not have to change how many pages/)).toBeInTheDocument();
  expect(screen.getByText(/Column break 2 in ¶1/)).toBeInTheDocument();
 },20000);

 it('reports a structural change that was never rendered as a failed run',async()=>{
  const unrendered={...BREAK_REVIEWABLE,state:'failed',error_code:'render_failure',
   error_detail:'An explicit-break removal requires a successful rendering of both the original and the candidate. The renderer is unavailable.',
   rendering_required:true,rendering_satisfied:false,candidate:null,break_targets:null,
   preview:{rendered:false,before_urls:[],after_urls:[],before_page_count:0,after_page_count:0,renderer_available:false},visual_comparison:null};
  mockApi({
   'POST /api/jobs':()=>({...unrendered,state:'queued'}),
   [`GET /api/jobs/${BREAK_JOB_ID}`]:()=>unrendered,
  });
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  fireEvent.click(await screen.findByRole('button',{name:/Diagnose with Gemma/}));
  expect(await screen.findByText(/That run/)).toBeInTheDocument();
  expect(screen.getByText(/requires a successful rendering/)).toBeInTheDocument();
  // Nothing to approve anywhere on the page.
  expect(screen.queryByRole('button',{name:/Accept/})).not.toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/Download/})).not.toBeInTheDocument();
 },20000);

 it('lists sections, names the one convertible change, and never invents a page number',async()=>{
  await openWorkspace();
  expect(screen.getByText(/starts nextPage \(schema default; no w:type written\)/)).toBeInTheDocument();
  expect(screen.getByText(/not page numbers/)).toBeInTheDocument();
  // The scope is stated, not implied: one conversion, and nothing else.
  expect(screen.getByText(/Only one section change exists/)).toBeInTheDocument();
  expect(screen.getByText(/cannot be changed/)).toBeInTheDocument();
 });

 it('offers only the eligible section and shows the other its refusal',async()=>{
  await openWorkspace();
  // Section 1 is selectable; section 0 is not a button at all, and says why.
  expect(screen.getByRole('button',{name:/Section 2 · starts nextPage/})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/Section 1 · starts nextPage/})).not.toBeInTheDocument();
  expect(screen.getByText(/there is no previous section here/)).toBeInTheDocument();
 });

 it('selecting a section is not a request to change it, and clears a selected break',async()=>{
  await openWorkspace();
  const section=screen.getByRole('button',{name:/Section 2 · starts nextPage/});
  await act(async()=>{fireEvent.click(section);});
  expect(screen.getByRole('button',{name:/Section 2 · starts nextPage/})).toHaveAttribute('aria-pressed','true');
  expect(screen.getByText(/not a request to change it/)).toBeInTheDocument();
  // Choosing a break instead releases the section: they are different repairs and
  // the server refuses a job that names both.
  const breakOption=screen.getAllByRole('button').find(b=>/break 1 of/i.test(b.textContent)&&b.getAttribute('aria-pressed')!==null);
  if(breakOption){
   await act(async()=>{fireEvent.click(breakOption);});
   expect(screen.getByRole('button',{name:/Section 2 · starts nextPage/})).toHaveAttribute('aria-pressed','false');
  }
 });
});

/* Seeing the document is a precondition for reviewing a change to it. A formatting
 * fault does not exist in a document's text, so a list of extracted paragraphs
 * cannot show a reader the thing they complained about. */
describe('the document itself',()=>{
 async function openWorkspace(overrides={}){
  const mock=mockApi(overrides);
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/})); await pick();
  await screen.findByText(/IN THIS DOCUMENT/);
  return mock;
 }

 it('renders the uploaded document before anything is diagnosed',async()=>{
  await openWorkspace();
  const pages=await screen.findAllByRole('img',{name:/Your document page/});
  expect(pages).toHaveLength(DOCUMENT_PREVIEW.page_count);
  expect(pages[0]).toHaveAttribute('src',DOCUMENT_PREVIEW.urls[0]);
  expect(screen.getByText(/RENDERED BY LIBREOFFICE/)).toBeInTheDocument();
  // A render of the original is not a proposal, and the page says so rather than
  // leaving a reader to assume something has already been changed.
  expect(screen.getByText(/Nothing here is a repair/)).toBeInTheDocument();
 });

 it('keeps the paragraph text reachable, because that is what carries the ids',async()=>{
  await openWorkspace();
  await screen.findAllByRole('img',{name:/Your document page/});
  const views=screen.getByRole('group',{name:'Document view'});
  await act(async()=>{fireEvent.click(within(views).getByRole('button',{name:'Text'}));});
  expect(screen.queryByRole('img',{name:/Your document page/})).not.toBeInTheDocument();
  // The evidence rows carry the paragraph ids the selection is made with, which is
  // the whole reason this view stays reachable.
  expect(screen.getByText('¶1')).toBeInTheDocument();
  expect(screen.getByText('¶2')).toBeInTheDocument();
 });

 it('falls back to the text and says why when the document cannot be rendered',async()=>{
  await openWorkspace({[`GET /api/documents/${DOCUMENT.document_id}/preview`]:()=>({
   ...DOCUMENT_PREVIEW,rendered:false,page_count:0,urls:[],renderer_available:false,
   unavailable_reason:'LibreOffice was not found, so the document cannot be rendered.'})});
  expect(await screen.findByText(/LibreOffice was not found/)).toBeInTheDocument();
  expect(screen.queryByRole('img',{name:/Your document page/})).not.toBeInTheDocument();
  // An absent renderer does not block a diagnosis, and the page must not imply it does.
  expect(screen.getByText(/a diagnosis is unaffected/)).toBeInTheDocument();
 });

 /* Gemma answered and the safety checks threw the answer away. That is the checks
  * working, so it must not read as a crash, and the server's own reason stays
  * available verbatim. */
 it('a refused answer reads as "no safe fix found", in plain words, with the reason kept',async()=>{
  const failed={...REVIEWABLE,state:'failed',diagnosis:null,proposal:null,preview:null,
   error_code:'invalid_response',error_detail:'duplicate evidence entry',
   attempts:1,max_attempts:1,discarded_attempts:[{attempt:1,error_code:'invalid_response',error:'duplicate evidence entry'}]};
  await openWorkspace({'POST /api/jobs':()=>({...failed,state:'queued'}),
                       [`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>failed});
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  expect(await screen.findByRole('heading',{name:/No safe fix\s*found\./})).toBeInTheDocument();
  expect(screen.queryByText(/That run/)).not.toBeInTheDocument();
  expect(screen.getByText(/listed the same piece of evidence twice/)).toBeInTheDocument();
  expect(screen.getByText(/Your original document is untouched/)).toBeInTheDocument();
  // The verbatim record is still there, folded under "Technical details".
  expect(screen.getByText('Technical details')).toBeInTheDocument();
  expect(screen.getByText(/Reason recorded: “duplicate evidence entry”/)).toBeInTheDocument();
  // Nothing to approve.
  expect(screen.queryByRole('button',{name:/Accept/})).not.toBeInTheDocument();
  // "Nothing was written" is never claimed: a failed run may have built a candidate.
  expect(screen.queryByText(/Nothing was written/)).not.toBeInTheDocument();
 },20000);

 it('an ineffective suggestion is explained as changing nothing on the page',async()=>{
  const failed={...REVIEWABLE,state:'failed',diagnosis:null,proposal:null,preview:null,
   error_code:'invalid_operation',error_detail:'Ineffective operation: every proposed operation writes a value this paragraph already resolves to, so nothing would change on the page. paragraph 0 page_break_before -> False is already off (no layer sets it)'};
  await openWorkspace({'POST /api/jobs':()=>({...failed,state:'queued'}),
                       [`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>failed});
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  expect(await screen.findByRole('heading',{name:/No safe fix\s*found\./})).toBeInTheDocument();
  expect(screen.getByText(/would not change anything on the page/)).toBeInTheDocument();
 },20000);

 it('a failure that is not a refused answer still says the run failed',async()=>{
  const failed={...REVIEWABLE,state:'failed',diagnosis:null,proposal:null,preview:null,
   error_code:'model_timeout',error_detail:'no answer within 600 s'};
  await openWorkspace({'POST /api/jobs':()=>({...failed,state:'queued'}),
                       [`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>failed});
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  expect(await screen.findByText(/That run/)).toBeInTheDocument();
  expect(screen.getByText(/did not answer in time/)).toBeInTheDocument();
  expect(screen.queryByText(/No safe fix/)).not.toBeInTheDocument();
 },20000);

 it('says nothing about a progress bar while Gemma runs',async()=>{
  await openWorkspace({[`GET /api/jobs/${REVIEWABLE.job_id}`]:()=>({...REVIEWABLE,state:'running',diagnosis:null,proposal:null,preview:null})});
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  expect(await screen.findByText(/usually takes 1–3 minutes/)).toBeInTheDocument();
  expect(screen.queryByText(/progress bar/i)).not.toBeInTheDocument();
 },20000);


 const route=(r)=>act(()=>{window.location.hash=r;window.dispatchEvent(new HashChangeEvent('hashchange'));});

 it('shows the proposed pages in the workspace, labelled as a proposal, with no download',async()=>{
  await openWorkspace();
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  await screen.findByText(/Direct 48pt where peers inherit 12pt/);
  const views=screen.getByRole('group',{name:'Document view'});
  expect(within(views).getAllByRole('button').map(b=>b.textContent)).toEqual(['Before','After','Text']);
  // A finished job switches the canvas to the candidate, and says it is not accepted.
  expect(await screen.findByRole('img',{name:'Proposed version page 1'})).toHaveAttribute('src',REVIEWABLE.preview.after_urls[0]);
  expect(screen.getByText('AFTER · PROPOSED, NOT ACCEPTED')).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/Download/})).not.toBeInTheDocument();
  // Before is the original as the job rendered it, the same picture the review uses.
  await act(async()=>{fireEvent.click(within(views).getByRole('button',{name:'Before'}));});
  expect(screen.getByRole('img',{name:'Your document page 1'})).toHaveAttribute('src',REVIEWABLE.preview.before_urls[0]);
  expect(screen.getByText('BEFORE · YOUR ORIGINAL')).toBeInTheDocument();
 },20000);

 it('after acceptance, shows before and after beside the download, and keeps it in the workspace',async()=>{
  await openWorkspace();
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Diagnose with Gemma/}));});
  await screen.findByText(/Direct 48pt where peers inherit 12pt/);
  route('review');
  fireEvent.click(await screen.findByRole('button',{name:/Accept this repair/}));
  const download=await screen.findByRole('link',{name:/Download repaired .docx/});
  expect(download).toHaveAttribute('href','/api/jobs/41abcdef0123/download');
  expect(screen.getByRole('img',{name:'Before page 1'})).toHaveAttribute('src',REVIEWABLE.preview.before_urls[0]);
  expect(screen.getByRole('img',{name:'After page 1'})).toHaveAttribute('src',REVIEWABLE.preview.after_urls[0]);
  // Back in the workspace the accepted job keeps its download instead of a blank form.
  route('workspace');
  expect(await screen.findByRole('heading',{name:/^Repair\s*accepted\.$/})).toBeInTheDocument();
  expect(screen.getAllByRole('link',{name:/Download/}).every(a=>a.getAttribute('href')==='/api/jobs/41abcdef0123/download')).toBe(true);
  expect(screen.getByText('AFTER · ACCEPTED REPAIR')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/Diagnose with Gemma/})).not.toBeInTheDocument();
  // The review page offers the same file once the job is accepted.
  route('review');
  expect(await screen.findByRole('link',{name:/Download repaired .docx/})).toHaveAttribute('href','/api/jobs/41abcdef0123/download');
 },20000);

 it('never shows one document’s pages for another opened while it was rendering',async()=>{
  const OTHER='0123456789abcdef0123456789abcdef';
  let releaseFirst;
  const slowFirst=new Promise(resolve=>{releaseFirst=()=>resolve(DOCUMENT_PREVIEW);});
  await openWorkspace({
   [`GET /api/documents/${DOCUMENT.document_id}/preview`]:()=>slowFirst,
   'POST /api/documents':()=>({...DOCUMENT,document_id:OTHER,origin:'upload',is_demo_document:false}),
   [`GET /api/documents/${OTHER}/paragraphs`]:()=>({...PARAGRAPHS,document_id:OTHER}),
   [`GET /api/documents/${OTHER}/breaks`]:()=>BREAKS,
   [`GET /api/documents/${OTHER}/sections`]:()=>SECTIONS,
   [`GET /api/documents/${OTHER}/preview`]:()=>({...DOCUMENT_PREVIEW,document_id:OTHER,page_count:1,urls:[`/api/documents/${OTHER}/preview/0`]}),
  });
  route('upload');
  const input=await screen.findByLabelText('Choose a Word document');
  await act(async()=>{fireEvent.change(input,{target:{files:[new File(['x'],'other.docx')]}});});
  await screen.findByRole('img',{name:'Your document page 1'});
  // The first document's render finishes late; it must not replace the open one.
  await act(async()=>{releaseFirst();await slowFirst;});
  const pages=screen.getAllByRole('img',{name:/Your document page/});
  expect(pages.map(p=>p.getAttribute('src'))).toEqual([`/api/documents/${OTHER}/preview/0`]);
 },20000);
});

/* The document appears the moment it opens: the browser draws the file itself, and
 * LibreOffice (tens of seconds on a CPU) is only the fallback. */
describe('instant preview',()=>{
 const open=async(overrides={})=>{
  const mock=mockApi(overrides);
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  return mock;
 };
 it('draws the sample from its own bytes without asking LibreOffice, and disarms script links',async()=>{
  const {calls}=await open({[`GET /api/documents/${DOCUMENT.document_id}/original`]:()=>new Blob(['PK the sample'])});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
  expect(await screen.findByText(/Drawn: the sample/)).toBeInTheDocument();
  expect(screen.getByText('YOUR DOCUMENT · INSTANT PREVIEW')).toBeInTheDocument();
  expect(screen.getByText(/drawn instantly by your browser/)).toBeInTheDocument();
  expect(calls.some(c=>c.key.endsWith('/preview'))).toBe(false);
  expect(screen.getByText('planted link')).not.toHaveAttribute('href');
 });
 it('draws an uploaded file from memory, with no round trip for its bytes',async()=>{
  const {calls}=await open();
  fireEvent.change(await screen.findByLabelText('Choose a Word document'),{target:{files:[new File(['PK my report'],'mine.docx')]}});
  expect(await screen.findByText(/Drawn: my report/)).toBeInTheDocument();
  expect(calls.some(c=>c.key.endsWith('/original'))).toBe(false);
  expect(calls.some(c=>c.key.endsWith('/preview'))).toBe(false);
 });
 it('falls back to the LibreOffice pages when the browser cannot draw the file',async()=>{
  await open({[`GET /api/documents/${DOCUMENT.document_id}/original`]:()=>new Blob(['not a docx'])});
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
  const pages=await screen.findAllByRole('img',{name:/Your document page/});
  expect(pages[0]).toHaveAttribute('src',DOCUMENT_PREVIEW.urls[0]);
  expect(screen.getByText(/RENDERED BY LIBREOFFICE/)).toBeInTheDocument();
 });
});

/* Nothing is chosen for the person: a complaint goes to the paragraph they picked, or
 * to every paragraph when they ask for the whole document. */
describe('choosing what to check',()=>{
 const open=async(overrides={})=>{
  const mock=mockApi(overrides);
  render(<App/>);
  act(()=>{window.location.hash='upload';window.dispatchEvent(new HashChangeEvent('hashchange'));});
  await screen.findByText(/CONNECTED/);
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
  await screen.findByText(/IN THIS DOCUMENT/);
  return mock;
 };
 it('selects no paragraph by default and says what to do first',async()=>{
  await open();
  expect(await screen.findByText(/First, click the paragraph that looks wrong/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Pick a paragraph first'})).toBeDisabled();
  expect(screen.queryByRole('button',{name:/Diagnose with Gemma/})).not.toBeInTheDocument();
  await pick();
  expect(screen.getByRole('button',{name:/Diagnose with Gemma/})).toBeEnabled();
  expect(screen.queryByText(/First, click the paragraph/)).not.toBeInTheDocument();
 });

 it('checks every paragraph one after another, and each result opens on its own',async()=>{
  const JOB={1:'a1abcdef0123',2:'b2abcdef0123'};
  const outcome={
   [JOB[1]]:{...REVIEWABLE,job_id:JOB[1],paragraph_id:1},
   [JOB[2]]:{...REVIEWABLE,job_id:JOB[2],paragraph_id:2,state:'failed',diagnosis:null,proposal:null,preview:null,error_code:'invalid_response',error_detail:'duplicate evidence entry'},
  };
  let inFlight=0,maxInFlight=0;
  const {calls}=await open({
   'POST /api/jobs':(options)=>{const body=JSON.parse(options.body);inFlight++;maxInFlight=Math.max(maxInFlight,inFlight);return {...outcome[JOB[body.paragraph_id]],state:'queued'};},
   [`GET /api/jobs/${JOB[1]}`]:()=>{inFlight=0;return outcome[JOB[1]];},
   [`GET /api/jobs/${JOB[2]}`]:()=>{inFlight=0;return outcome[JOB[2]];},
  });
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Check every paragraph/}));});
  expect(screen.getByRole('button',{name:/Check every paragraph/})).toHaveAttribute('aria-pressed','true');
  expect(screen.getByText(/Gemma checks the 2 paragraphs with text one at a time/)).toBeInTheDocument();
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/Check all 2 paragraphs/}));});
  expect(await screen.findByText(/2 of 2 checked/,{},{timeout:10000})).toBeInTheDocument();
  // One job per paragraph, the same request a single paragraph sends, never two at once.
  const posted=calls.filter(c=>c.key==='POST /api/jobs').map(c=>JSON.parse(c.options.body));
  expect(posted.map(b=>b.paragraph_id)).toEqual([1,2]);
  posted.forEach(b=>expect(b).toMatchObject({document_id:DOCUMENT.document_id,expected_source_sha256:'ca278f'}));
  expect(maxInFlight).toBe(1);
  expect(screen.getByRole('heading',{name:/1 fix\s*to review\./})).toBeInTheDocument();
  expect(screen.getByText('Fix ready to review')).toBeInTheDocument();
  expect(screen.getByText('No safe fix found')).toBeInTheDocument();
  // Opening a result shows that paragraph's own review, with a way back to the list.
  await act(async()=>{fireEvent.click(screen.getByText('Fix ready to review').closest('button'));});
  expect(await screen.findByRole('heading',{name:/A candidate\s*to review\./})).toBeInTheDocument();
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:/All paragraphs/}));});
  expect(screen.getByText('No safe fix found')).toBeInTheDocument();
  await act(async()=>{fireEvent.click(screen.getByText('No safe fix found').closest('button'));});
  expect(await screen.findByRole('heading',{name:/No safe fix\s*found\./})).toBeInTheDocument();
 },30000);
});
