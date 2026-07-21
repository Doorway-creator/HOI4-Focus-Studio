(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.TechnologyLayout=api})(typeof globalThis!=='undefined'?globalThis:this,function(){
  const finite=value=>Number.isFinite(Number(value))?Number(value):null;
  function diagnostic(context,item,field,message){return{sourceEnvironment:context.sourceEnvironment||'',country:context.country||'',category:context.category||'',technologyId:String(item?.entity_id||'<missing id>'),missingField:field,message,sourceFile:String(item?.source_file||''),layoutFile:String(item?.normalized?.interfaceFile||item?.normalized?.layoutSource||'')}}
  const rules={
    bba_air_techs_folder:[['airframes',/(?:small|medium|large).*airframe|airship|helicopter|transport_plane/],['engines',/engine|jet|propulsion/],['weapons',/cannon|machine_gun|mg_|rocket|missile|weapon/],['naval-air',/torpedo|naval|maritime|carrier/],['defensive',/turret|armor|armour|surviv|extinguisher|defen/],['recon-electronics',/radar|recon|camera|radio|navigation|computer|electronics/],['construction',/construction|bomb|airframe|wing|fuel|material/]],
    nsb_armour_folder:[['chassis',/chassis|tankette/],['engines',/engine|diesel|petrol|gasoline/],['armour',/armor|armour|weld|cast/],['suspension',/suspension|torsion|bogie/],['turrets',/turret/],['guns-modules',/cannon|gun|howitzer|module|radio/],['amphibious',/amphib|flame|special/]],
    infantry_folder:[['infantry-weapons',/infantry|weapon|rifle/],['special-forces',/marine|mountain|paratroop|special_forces/],['environment',/clothing|winter|jungle|desert|arctic/],['support-weapons',/support|machine|mortar/],['night-vision',/night|vision/],['rockets-at',/rocket|anti_tank|at_/]],
    support_folder:[['engineers',/engineer/],['recon',/recon/],['medical-logistics',/hospital|medical|logistic|maintenance/],['signals',/signal|radio/],['military-police',/police|mp_/],['special-support',/.*/]],
    mtgnavalsupportfolder:[['weapons',/gun|battery|cannon|torpedo/],['mines',/mine/],['submarine',/submarine|snorkel/],['sensors',/radar|sonar|fire_control/],['support-logistics',/fuel|damage|transport|logistic/],['special-projects',/.*/]],
    r56_vechicles_folder:[['rail',/railway|rail_/],['armoured-train',/armored_train|armoured_train/],['railway-gun',/railway_gun/],['motorisation',/motor|truck|vehicle/],['trains',/.*/]]
  };
  function categoryStrategy(category,item){const id=String(item?.entity_id||'').toLowerCase(),sets=rules[category]||[];for(let index=0;index<sets.length;index++)if(sets[index][1].test(id))return{name:sets[index][0],lane:index};return{name:'source',lane:0}}
  function layout(items,context={},forceFallback=false){
    const positions=new Map(),warnings=[],occupied=[],renderable=[],category=context.category||'';
    for(const[index,item]of(Array.isArray(items)?items:[]).entries()){
      if(!item||typeof item!=='object'){warnings.push(diagnostic(context,item,'technology','Technology entry is missing or malformed.'));continue}
      const id=String(item.entity_id||'').trim();if(!id){warnings.push(diagnostic(context,item,'technology ID','Technology has no usable ID.'));continue}
      const raw=item.normalized?.position,px=finite(raw?.x),py=finite(raw?.y),sourcePlaced=!forceFallback&&raw&&typeof raw==='object'&&px!==null&&py!==null&&Boolean(item.normalized?.layoutSource||item.source_file);
      const strategy=categoryStrategy(category,item),year=finite(item.normalized?.year)??1936;
      let x,y,placement;
      if(sourcePlaced){
        const grouped=(rules[category]||[]).length>0;
        x=grouped?170+strategy.lane*245+px*14:170+px*150;
        y=130+py*106;
        placement=grouped?'source-category':'source';
      }else{
        x=170+(index%7)*210;y=130+Math.floor(index/7)*124+(year-1936)*3;placement='fallback';
        warnings.push(diagnostic(context,item,!raw?'layout entry':px===null?'x':'y','Using collision-free fallback placement.'));
      }
      if(!Number.isFinite(x)||!Number.isFinite(y)){x=170+(index%7)*210;y=130+Math.floor(index/7)*124;placement='fallback';warnings.push(diagnostic(context,item,'normalized coordinates','Non-finite coordinates were replaced.'))}
      while(occupied.some(point=>Math.abs(point.x-x)<190&&Math.abs(point.y-y)<92)){x+=groupedStep(placement);if(x>2400){x=170;y+=116}}
      const point={x,y,placement,group:strategy.name};positions.set(id,point);occupied.push(point);renderable.push(item);item.layoutPlacement=placement;item.layoutGroup=strategy.name;
    }
    const visibleIds=new Set(positions.keys()),edges=[];
    for(const item of renderable){const target=positions.get(String(item.entity_id));if(!target)continue;for(const parentId of(Array.isArray(item.normalized?.prerequisites)?item.normalized.prerequisites:[])){const source=positions.get(String(parentId));if(source)edges.push({from:String(parentId),to:String(item.entity_id),source,target});else warnings.push(diagnostic(context,item,`prerequisite ${parentId}`,visibleIds.has(String(parentId))?'Prerequisite has no normalized position.':'Prerequisite is outside the selected or filtered category.'))}}
    return{positions,warnings,renderable,edges,empty:renderable.length===0,metrics:{sourcePlaced:[...positions.values()].filter(x=>x.placement!=='fallback').length,fallbackPlaced:[...positions.values()].filter(x=>x.placement==='fallback').length,strategy:rules[category]?'category-specific':'source-grid'}}
  }
  function groupedStep(placement){return placement==='fallback'?210:198}
  return{finite,diagnostic,categoryStrategy,layout};
});
