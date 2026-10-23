import React from 'react';
import {describe,it,expect,vi,beforeEach,afterEach} from 'vitest';
import {render,screen,fireEvent,act,waitFor} from '@testing-library/react';
import App from './App';
import * as api from './api';

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

function mockApi(overrides={}){
 const calls=[];
 const routes={
  'GET /api/health':()=>HEALTH_LIVE,
  'POST /api/documents':()=>({...DOCUMENT,origin:'upload',is_demo_document:false}),
  'POST /api/demo/document':()=>DOCUMENT,
  [`GET /api/documents/${DOCUMENT.document_id}/paragraphs`]:()=>PARAGRAPHS,
  [`GET /api/documents/${DOCUMENT.document_id}/breaks`]:()=>BREAKS,
  [`GET /api/documents/${DOCUMENT.document_id}/sections`]:()=>SECTIONS,
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
  return {ok:true,status:200,json:async()=>result};
 });
 vi.stubGlobal('fetch',fetchMock);
 return {calls,fetchMock};
}

afterEach(()=>{vi.unstubAllGlobals();});

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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));

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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
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
  fireEvent.click(await screen.findByRole('button',{name:/Build the sample/}));
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
