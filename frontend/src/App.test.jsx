import React from 'react';
import {describe,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,act,within} from '@testing-library/react';
import App from './App';
import {validateFile,readHistory} from './data';
function route(name){act(()=>{window.location.hash=name;window.dispatchEvent(new HashChangeEvent('hashchange'));});}
function click(text){fireEvent.click(screen.getByRole('button',{name:text}));}
function sample(){route('workspace');click('Open the sample demo');}
function proposal(){sample();click('Preview a repair');act(()=>vi.advanceTimersByTime(1800));route('review');}
describe('review boundaries',()=>{
 it('requires a proposal before review and explicit acceptance before results',()=>{render(<App/>);sample();route('review');expect(screen.getByText('Nothing to review yet.')).toBeInTheDocument();route('results');expect(screen.getByText('A preview isn’t an approval.')).toBeInTheDocument();expect(screen.queryByRole('button',{name:/Download/})).not.toBeInTheDocument();});
 it('supports diagnosis, side-by-side review, approval, receipt and persisted history',()=>{vi.useFakeTimers();render(<App/>);proposal();expect(screen.getByLabelText('Original sample document')).toBeInTheDocument();expect(screen.getByLabelText('Proposed sample document')).toBeInTheDocument();click('Accept sample repair');route('results');expect(screen.getByText('No · frontend demo')).toBeInTheDocument();expect(readHistory()).toHaveLength(1);route('review');expect(screen.queryByRole('button',{name:'Accept sample repair'})).not.toBeInTheDocument();route('history');expect(screen.getByText('Accepted')).toBeInTheDocument();click('Clear demo history');expect(readHistory()).toHaveLength(0);});
 it('rejects a candidate without making a result available',()=>{vi.useFakeTimers();render(<App/>);proposal();click('Keep original');route('workspace');expect(screen.getByText('Original kept')).toBeInTheDocument();route('results');expect(screen.queryByRole('button',{name:/Download/})).not.toBeInTheDocument();expect(readHistory()).toHaveLength(0);});
 it('cancels the simulation when leaving the workspace',()=>{vi.useFakeTimers();render(<App/>);sample();click('Preview a repair');route('upload');act(()=>vi.advanceTimersByTime(3000));route('review');expect(screen.getByText('Nothing to review yet.')).toBeInTheDocument();});
 it('cancel button stops progress and returns to complaint editing',()=>{vi.useFakeTimers();render(<App/>);sample();click('Preview a repair');click('Cancel analysis');act(()=>vi.advanceTimersByTime(3000));expect(screen.getByLabelText('What looks wrong?')).toBeInTheDocument();expect(screen.queryByText(/PROPOSAL READY/)).not.toBeInTheDocument();});
 it('shows clarification for unsupported requests',()=>{render(<App/>);sample();click('Wrong alignment');click('Preview a repair');route('clarify');expect(screen.getByText('Wrong alignment')).toBeInTheDocument();expect(screen.getByText('ONE QUICK QUESTION')).toBeInTheDocument();});
 it('switches comparison views',()=>{vi.useFakeTimers();render(<App/>);proposal();click('Proposed');expect(screen.queryByLabelText('Original sample document')).not.toBeInTheDocument();click('Original');expect(screen.queryByLabelText('Proposed sample document')).not.toBeInTheDocument();});
});
describe('files and navigation',()=>{
 it('rejects wrong type, empty and oversize files',()=>{expect(validateFile({name:'x.pdf',size:1})).toMatch(/docx/);expect(validateFile({name:'x.docx',size:0})).toMatch(/empty/);expect(validateFile({name:'x.docx',size:21*1024*1024})).toMatch(/20 MB/);expect(validateFile({name:'x.DOCX',size:20})).toBeNull();});
 it('requires a connected backend or an explicit sample choice before document work',async()=>{const fetchSpy=vi.spyOn(globalThis,'fetch');render(<App/>);route('upload');expect(screen.getByText('Choose how to continue.')).toBeInTheDocument();expect(screen.queryByLabelText('Original sample document')).not.toBeInTheDocument();expect(fetchSpy.mock.calls.filter(([url])=>String(url).includes('/api/documents'))).toHaveLength(0);click('Open the sample demo');expect(await screen.findByLabelText('Original sample document')).toBeInTheDocument();});
 it('renders every ancillary page and an unknown route',()=>{render(<App/>);for(const r of ['home','upload','history','settings','guide','privacy','design','not-a-page']){route(r);expect(screen.getByRole('main')).toBeInTheDocument();}expect(screen.getByText('This page wandered off.')).toBeInTheDocument();});
 it('leaves the skip link reachable on first load and focuses main only on later routes',()=>{
  // StrictMode, as main.jsx mounts it: it runs mount effects twice, which defeats a
  // render-counting guard and is how this defect survived its first fix.
  render(<React.StrictMode><App/></React.StrictMode>);
  // A skip link exists to be the first thing a keyboard user reaches. Focusing main
  // on the initial render would steal that first Tab stop and strand the link, so
  // the first render must leave focus where the browser put it.
  expect(document.activeElement).toBe(document.body);
  const skip=screen.getByRole('link',{name:'Skip to content'});
  expect(skip).toBe(document.querySelector('.app a, .app button, .app input'));
  fireEvent.click(skip);
  expect(document.activeElement).toBe(screen.getByRole('main'));
  // A route change is a different case: nothing announces new content to a screen
  // reader in a single-page app, so focus does move there.
  route('upload');
  expect(document.activeElement).toBe(screen.getByRole('main'));
 });
 it('tolerates malformed stored history',()=>{localStorage.setItem('wtd-demo-history','[null, {"date":"bad"}]');expect(readHistory()).toEqual([]);localStorage.setItem('wtd-demo-history','{');expect(readHistory()).toEqual([]);});
 it('preserves sample words across the comparison',()=>{vi.useFakeTimers();render(<App/>);proposal();const before=screen.getByLabelText('Original sample document'),after=screen.getByLabelText('Proposed sample document');expect(within(before).getByText(/Good ideas deserve/).textContent).toEqual(within(after).getByText(/Good ideas deserve/).textContent);expect(before.querySelector('.broken-spacing')).not.toBeNull();expect(after.querySelector('.broken-spacing')).toBeNull();});
});
