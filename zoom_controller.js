(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioZoom=api})(typeof globalThis!=='undefined'?globalThis:this,function(){
  const finite=(value,fallback)=>Number.isFinite(Number(value))?Number(value):fallback;
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,finite(value,min)));
  function anchoredScroll(scrollLeft,scrollTop,anchorX,anchorY,oldScale,newScale){
    const old=Math.max(.01,finite(oldScale,1)),next=Math.max(.01,finite(newScale,1));
    return{left:(finite(scrollLeft,0)+finite(anchorX,0))*next/old-finite(anchorX,0),top:(finite(scrollTop,0)+finite(anchorY,0))*next/old-finite(anchorY,0)};
  }
  function create(options){
    const min=finite(options.min,.35),max=finite(options.max,1.6);
    function set(value,clientX=null,clientY=null){
      const view=options.view(),old=finite(options.getScale(),1),next=clamp(value,min,max),rect=view.getBoundingClientRect();
      const anchorX=clientX===null?view.clientWidth/2:clientX-rect.left,anchorY=clientY===null?view.clientHeight/2:clientY-rect.top;
      const scroll=anchoredScroll(view.scrollLeft,view.scrollTop,anchorX,anchorY,old,next);
      options.apply(next);
      view.scrollLeft=scroll.left;view.scrollTop=scroll.top;
      options.changed?.(next);
      return next;
    }
    function step(direction,clientX=null,clientY=null,factor=1.1){return set(options.getScale()*(direction>0?factor:1/factor),clientX,clientY)}
    return{set,step,min,max};
  }
  return{finite,clamp,anchoredScroll,create};
});
