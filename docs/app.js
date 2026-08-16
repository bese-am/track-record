const tip=document.getElementById('tip');
document.querySelectorAll('.hit').forEach(el=>{
 el.addEventListener('mouseenter',()=>{
  const c=el.dataset.c,x=+el.dataset.x,y=+el.dataset.y;
  const cr=document.getElementById('c-'+c),fo=document.getElementById('f-'+c);
  if(cr){cr.setAttribute('x1',x);cr.setAttribute('x2',x);cr.style.display='';}
  if(fo){fo.setAttribute('cx',x);fo.setAttribute('cy',y);fo.style.display='';}
  tip.replaceChildren(Object.assign(document.createElement('b'),
  {textContent:el.dataset.title}),document.createTextNode(el.dataset.body));tip.style.opacity='1';});
 el.addEventListener('mousemove',e=>{
  const tw=tip.offsetWidth||230;
  tip.style.left=Math.max(8,Math.min(e.clientX+14,innerWidth-tw-8))+'px';
  tip.style.top=(e.clientY-12)+'px';});
 el.addEventListener('mouseleave',()=>{tip.style.opacity='0';
  document.querySelectorAll('.cross,.focus').forEach(n=>n.style.display='none');});
});
const place=(e)=>{const tw=tip.offsetWidth||230;
 tip.style.left=Math.max(8,Math.min(e.clientX+14,innerWidth-tw-8))+'px';
 tip.style.top=(e.clientY-12)+'px';};
document.querySelectorAll('.strip .seg').forEach(el=>{
 el.removeAttribute('title');
 el.addEventListener('mouseenter',e=>{
  tip.replaceChildren(
   Object.assign(document.createElement('b'),{textContent:el.dataset.title}),
   Object.assign(document.createElement('span'),
    {className:'h',textContent:el.dataset.hash}));
  place(e);tip.style.opacity='1';});
 el.addEventListener('mousemove',place);
 el.addEventListener('mouseleave',()=>{tip.style.opacity='0';});
});
