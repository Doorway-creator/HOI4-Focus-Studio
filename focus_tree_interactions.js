(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;root.FocusTreeInteractions=api})(typeof globalThis!=='undefined'?globalThis:this,function(){
  function normalizedRect(a,b){return{left:Math.min(a.x,b.x),right:Math.max(a.x,b.x),top:Math.min(a.y,b.y),bottom:Math.max(a.y,b.y)}}
  function rectIntersects(a,b){return a.left<=b.right&&a.right>=b.left&&a.top<=b.bottom&&a.bottom>=b.top}
  function focusBounds(f,nodePosition,halfWidth=78,halfHeight=37){const p=nodePosition(f);return{left:p.x-halfWidth,right:p.x+halfWidth,top:p.y-halfHeight,bottom:p.y+halfHeight}}
  function focusIdsInRectangle(focuses,rectangle,nodePosition){return focuses.filter(f=>rectIntersects(rectangle,focusBounds(f,nodePosition))).map(f=>f.id)}
  function quantizedDelta(value,quarters=4){return Math.round(value*quarters)/quarters}
  function toggleSelection(selectedIds,id){const ids=new Set(selectedIds);if(ids.has(id))ids.delete(id);else ids.add(id);return[...ids]}
  function connectFocuses(focuses,from,to,type){const target=focuses.find(f=>f.id===to),source=focuses.find(f=>f.id===from);if(!source||!target||from===to)return false;if(type==='prerequisite'){target.prerequisites??=[];if(!target.prerequisites.includes(from))target.prerequisites.push(from)}else if(type==='visual'){target.visualConnections??=[];if(!target.visualConnections.includes(from))target.visualConnections.push(from)}else if(type==='mutual'){target.mutuallyExclusive??=[];source.mutuallyExclusive??=[];if(!target.mutuallyExclusive.includes(from))target.mutuallyExclusive.push(from);if(!source.mutuallyExclusive.includes(to))source.mutuallyExclusive.push(to)}else return false;return true}
  function moveFocusGroup(focuses,selectedIds,origins,deltaX,deltaY){const ids=new Set(selectedIds),dx=quantizedDelta(deltaX),dy=quantizedDelta(deltaY);for(const focus of focuses){if(!ids.has(focus.id))continue;const origin=origins[focus.id];if(!origin)continue;focus.x=origin.x+dx;focus.y=origin.y+dy}return{dx,dy}}
  return{normalizedRect,rectIntersects,focusBounds,focusIdsInRectangle,quantizedDelta,toggleSelection,connectFocuses,moveFocusGroup}
});
