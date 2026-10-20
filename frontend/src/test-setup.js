import '@testing-library/jest-dom/vitest';
import {afterEach,vi} from 'vitest';
import {cleanup} from '@testing-library/react';
window.scrollTo=vi.fn();
afterEach(()=>{cleanup();localStorage.clear();window.history.replaceState(null,'','/');vi.useRealTimers();});
