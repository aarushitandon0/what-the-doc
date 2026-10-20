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
