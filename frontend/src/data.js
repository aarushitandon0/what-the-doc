export const paragraphs = [
 {title:'Introduction',text:'Good ideas deserve a clear page. This report explores how thoughtful design helps people spend less time on repetitive work.'},
 {title:'Methodology',text:'We observed everyday document tasks and compared the formatting of equivalent headings. Each proposed change was reviewed before acceptance.'},
 {title:'Findings',text:'Small inconsistencies create unnecessary friction. A targeted adjustment can restore the rhythm of a page while preserving the author’s words.'},
 {title:'Conclusion',text:'The best tools give people their time back. A readable document should be the beginning of the next task, not another problem to solve.'}
];
export const steps = [ ['01','Drop it','Bring your Word document. Keep a copy of the original.'], ['02','Point it out','Tell us what looks wrong. Review a precise proposal.'], ['03','Keep the fix','Compare the pages, approve the change, and get on with your day.'] ];
export function validateFile(file){if(!file)return 'Choose a document first.';if(!/\.docx$/i.test(file.name))return 'Please choose a .docx Word document.';if(file.size===0)return 'This file is empty. Choose another document.';if(file.size>20*1024*1024)return 'Please choose a document no larger than 20 MB.';return null;}
export function readHistory(){try{const h=JSON.parse(localStorage.getItem('wtd-demo-history')||'[]');return Array.isArray(h)?h.filter(x=>x&&typeof x.id==='string'&&typeof x.date==='string'&&!Number.isNaN(Date.parse(x.date))).slice(0,20):[];}catch{return [];}}
export const report = {mode:'frontend-demo',document:'Research-report.docx',change:{paragraph:'Methodology',property:'space_before_pt',before:48,after:12},actual_docx_generated:false};
