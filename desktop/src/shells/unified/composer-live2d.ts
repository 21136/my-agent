function assetRoot(): URL { return new URL("live2d/chibi-front/", document.baseURI); }
function localAsset(path: string): URL {
  if (!path || path.startsWith("/") || path.includes("\\") || path.split("/").some(part => part === ".." || part === ".")) {
    throw new Error("Rejected non-local Live2D asset path");
  }
  const root = assetRoot();
  const url = new URL(path, root);
  if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) {
    throw new Error("Rejected Live2D asset outside the bundled directory");
  }
  return url;
}

function cubic(a: number, b: number, c: number, d: number, t: number): number {
  const q = 1 - t;
  return q*q*q*a + 3*q*q*t*b + 3*q*t*t*c + t*t*t*d;
}

function solveBezierTime(time: number, t0: number, t1: number, t2: number, t3: number): number {
  let low = 0, high = 1;
  for (let i=0; i<20; i+=1) {
    const mid=(low+high)/2;
    if (cubic(t0,t1,t2,t3,mid)<time) low=mid; else high=mid;
  }
  return (low+high)/2;
}

export function evaluateIdleCurve(curve: {Segments?: number[]}, time: number): number {
  const segments=curve.Segments;
  if (!segments || segments.length<2) return 0;
  let t0=segments[0], v0=segments[1], i=2;
  while(i<segments.length){
    const type=segments[i++];
    if(type===1){
      if(i+5>=segments.length) break;
      const t1=segments[i++],v1=segments[i++],t2=segments[i++],v2=segments[i++],t3=segments[i++],v3=segments[i++];
      if(time<=t3) return cubic(v0,v1,v2,v3,solveBezierTime(time,t0,t1,t2,t3));
      t0=t3;v0=v3;
    } else {
      if(i+1>=segments.length) break;
      const t1=segments[i++],v1=segments[i++];
      if(time<=t1){
        if(type===2)return v0;
        if(type===3)return v1;
        const ratio=Math.max(0,Math.min(1,(time-t0)/(t1-t0||1)));
        return v0+(v1-v0)*ratio;
      }
      t0=t1;v0=v1;
    }
  }
  return v0;
}

export function applyIdleMotionParameters(
  parameters: {ids: string[]; values: Float32Array},
  motion: {Curves?: Array<{Target: string; Id: string; Segments?: number[]}>},
  baseline: Float32Array,
  time: number,
): void {
  parameters.values.set(baseline);
  for(const curve of motion.Curves ?? []){
    if(curve.Target!=="Parameter") continue;
    const index=parameters.ids.indexOf(curve.Id);
    if(index>=0) parameters.values[index]=evaluateIdleCurve(curve,time);
  }
}

async function loadCore(): Promise<void> {
  const w = window as typeof window & { Live2DCubismCore?: any };
  if (w.Live2DCubismCore) return;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = localAsset("live2dcubismcore.min.js").href;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Cubism Core load failed"));
    document.head.append(script);
  });
  if (!w.Live2DCubismCore) throw new Error("Cubism Core unavailable");
}

async function readJson(path: string): Promise<any> {
  const response = await fetch(localAsset(path));
  if (!response.ok) throw new Error(`Live2D asset unavailable: ${path}`);
  return response.json();
}

function drawTriangle(ctx: CanvasRenderingContext2D, image: HTMLImageElement, points: Array<{x:number;y:number;u:number;v:number}>, opacity: number): void {
  const [a,b,c]=points,du1=b.u-a.u,dv1=b.v-a.v,du2=c.u-a.u,dv2=c.v-a.v;
  const determinant=du1*dv2-du2*dv1;
  if(!determinant)return;
  const dx1=b.x-a.x,dy1=b.y-a.y,dx2=c.x-a.x,dy2=c.y-a.y;
  const m11=(dx1*dv2-dx2*dv1)/determinant,m12=(dy1*dv2-dy2*dv1)/determinant;
  const m21=(dx2*du1-dx1*du2)/determinant,m22=(dy2*du1-dy1*du2)/determinant;
  ctx.save();ctx.globalAlpha=opacity;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.lineTo(c.x,c.y);ctx.closePath();ctx.clip();
  ctx.transform(m11,m12,m21,m22,a.x-m11*a.u-m21*a.v,a.y-m12*a.u-m22*a.v);ctx.drawImage(image,0,0);ctx.restore();
}

type MaskDrawableSet = { count:number; maskCounts:ArrayLike<number>; masks:ArrayLike<ArrayLike<number>>; constantFlags:ArrayLike<number> };
type MaskGroup = { maskIds:number[]; inverted:boolean; drawableIndices:number[] };
const CUBISM_BLEND_ADDITIVE=1, CUBISM_BLEND_MULTIPLICATIVE=2, CUBISM_MASK_INVERTED=8;

export function blendModeForFlags(flags:number): GlobalCompositeOperation {
  if(flags & CUBISM_BLEND_ADDITIVE)return "lighter";
  if(flags & CUBISM_BLEND_MULTIPLICATIVE)return "multiply";
  return "source-over";
}
export function drawableUsesInvertedMask(flags:number):boolean { return (flags & CUBISM_MASK_INVERTED)!==0; }
export function buildMaskGroups(drawables:MaskDrawableSet):Map<string,MaskGroup> {
  const groups=new Map<string,MaskGroup>();
  for(let i=0;i<drawables.count;i++){
    const count=drawables.maskCounts[i]??0;
    if(count<=0)continue;
    const ids=Array.from(drawables.masks[i]??[]).slice(0,count);
    if(ids.some(id=>!Number.isInteger(id)||id<0||id>=drawables.count))throw new Error("Invalid Cubism mask drawable index");
    const inverted=drawableUsesInvertedMask(drawables.constantFlags[i]??0),key=`${inverted?"i":"n"}:${ids.join(",")}`;
    const group=groups.get(key)??{maskIds:ids,inverted,drawableIndices:[]};
    group.drawableIndices.push(i);groups.set(key,group);
  }
  return groups;
}
export function compositeDrawableMask(ctx:CanvasRenderingContext2D,mask:CanvasImageSource,inverted:boolean):void {
  ctx.save();ctx.globalCompositeOperation=inverted?"destination-out":"destination-in";ctx.drawImage(mask,0,0);ctx.restore();
}

function drawDrawableMesh(ctx:CanvasRenderingContext2D,texture:HTMLImageElement,drawables:any,index:number,width:number,height:number,scale:number,centerX:number,centerY:number):void {
  const positions=drawables.vertexPositions[index],uvs=drawables.vertexUvs[index],indices=drawables.indices[index];
  if(!positions||!uvs||!indices)return;
  for(let i=0;i+2<indices.length;i+=3){
    const triangle=[indices[i],indices[i+1],indices[i+2]].map((vertex:number)=>({x:width/2+(positions[vertex*2]-centerX)*scale,y:height/2-(positions[vertex*2+1]-centerY)*scale,u:uvs[vertex*2]*texture.width,v:(1-uvs[vertex*2+1])*texture.height}));
    drawTriangle(ctx,texture,triangle,drawables.opacities[index]??1);
  }
}
function sizeCanvas(target:HTMLCanvasElement,width:number,height:number):void {
  if(target.width!==width||target.height!==height){target.width=width;target.height=height;}
}

export function mountComposerLive2D(container: HTMLElement): () => void {
  const canvas=container.querySelector<HTMLCanvasElement>(".unified-composer-live2d");
  const fallback=container.querySelector<HTMLImageElement>("img");
  if(!canvas||!fallback)return()=>{};
  const context=canvas.getContext("2d"),reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)");
  if(!context||reduceMotion.matches)return()=>{};

  let frame=0,disposed=false,ready=false,moc:any,model:any,texture:HTMLImageElement;
  let bounds:{x1:number;x2:number;y1:number;y2:number}|undefined;
  let baseline:Float32Array,motion:any,motionStartedAt=0,maskGroups=new Map<string,MaskGroup>(),drawableMaskKeys=new Map<number,string>();
  const drawableCanvas=document.createElement("canvas"),maskSourceCanvas=document.createElement("canvas");
  const drawableContext=drawableCanvas.getContext("2d"),maskSourceContext=maskSourceCanvas.getContext("2d");
  const maskCanvases=new Map<string,HTMLCanvasElement>();
  if(!drawableContext||!maskSourceContext)throw new Error("Canvas offscreen contexts unavailable");
  const stopFrame=()=>{if(frame){cancelAnimationFrame(frame);frame=0;}};
  const releaseModel=()=>{model?.release?.();model=null;moc?._release?.();moc=null;};
  const dispose=()=>{
    if(disposed)return;
    disposed=true;stopFrame();ready=false;
    document.removeEventListener("visibilitychange",onVisibilityChange);
    reduceMotion.removeEventListener("change",onMotionChange);
    resizeObserver.disconnect();releaseModel();canvas.hidden=true;fallback.hidden=false;
  };
  const schedule=()=>{if(!disposed&&ready&&!document.hidden&&!frame)frame=requestAnimationFrame(drawFrame);};
  const onVisibilityChange=()=>{if(document.hidden)stopFrame();else{motionStartedAt=performance.now();schedule();}};
  const onMotionChange=()=>{if(reduceMotion.matches)dispose();};
  const resizeObserver=new ResizeObserver(()=>schedule());
  const drawFrame=()=>{
    frame=0;if(disposed||!ready||document.hidden)return;
    try{
      const rect=canvas.getBoundingClientRect(),ratio=Math.min(window.devicePixelRatio||1,2);
      const width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));
      sizeCanvas(canvas,width,height);sizeCanvas(drawableCanvas,width,height);sizeCanvas(maskSourceCanvas,width,height);
      for(const layer of maskCanvases.values())sizeCanvas(layer,width,height);
      context.clearRect(0,0,width,height);
      const elapsed=((performance.now()-motionStartedAt)/1000)%motion.Meta.Duration;
      applyIdleMotionParameters(model.parameters,motion,baseline,elapsed);
      model.update();
      const d=model.drawables;
      if(!bounds){
        let x1=Infinity,x2=-Infinity,y1=Infinity,y2=-Infinity;
        for(const vertices of d.vertexPositions)if(vertices)for(let i=0;i<vertices.length;i+=2){x1=Math.min(x1,vertices[i]);x2=Math.max(x2,vertices[i]);y1=Math.min(y1,vertices[i+1]);y2=Math.max(y2,vertices[i+1]);}
        if(![x1,x2,y1,y2].every(Number.isFinite)||x2<=x1||y2<=y1)throw new Error("Invalid model bounds");
        bounds={x1,x2,y1,y2};
      }
      const scale=Math.min(width/(bounds.x2-bounds.x1),height/(bounds.y2-bounds.y1))*0.82;
      const centerX=(bounds.x1+bounds.x2)/2,centerY=bounds.y1+(bounds.y2-bounds.y1)*0.5;
      const order=Array.from(d.renderOrders,(value:number,index:number)=>({value,index})).sort((a,b)=>a.value-b.value);
      const currentMasks=new Map<string,HTMLCanvasElement>();
      for(const [key,group] of maskGroups){
        let layer=maskCanvases.get(key);
        if(!layer){layer=document.createElement("canvas");maskCanvases.set(key,layer);sizeCanvas(layer,width,height);}
        const layerContext=layer.getContext("2d");if(!layerContext)throw new Error("Cubism mask context unavailable");
        layerContext.clearRect(0,0,width,height);
        for(const maskIndex of group.maskIds){
          if(d.dynamicFlags&&!(d.dynamicFlags[maskIndex]&1))continue;
          maskSourceContext.clearRect(0,0,width,height);
          drawDrawableMesh(maskSourceContext,texture,d,maskIndex,width,height,scale,centerX,centerY);
          layerContext.save();layerContext.globalCompositeOperation="source-over";layerContext.drawImage(maskSourceCanvas,0,0);layerContext.restore();
        }
        currentMasks.set(key,layer);
      }
      for(const {index} of order){
        if(d.dynamicFlags&&!(d.dynamicFlags[index]&1))continue;
        const maskKey=drawableMaskKeys.get(index),blend=blendModeForFlags(d.constantFlags[index]??0);
        if(maskKey){
          drawableContext.clearRect(0,0,width,height);
          drawDrawableMesh(drawableContext,texture,d,index,width,height,scale,centerX,centerY);
          const mask=currentMasks.get(maskKey);if(!mask)throw new Error("Cubism mask group was not rendered");
          compositeDrawableMask(drawableContext,mask,drawableUsesInvertedMask(d.constantFlags[index]??0));
          context.save();context.globalCompositeOperation=blend;context.drawImage(drawableCanvas,0,0);context.restore();
        }else{
          context.save();context.globalCompositeOperation=blend;
          drawDrawableMesh(context,texture,d,index,width,height,scale,centerX,centerY);
          context.restore();
        }
      }
      schedule();
    }catch(error){console.warn("Composer Live2D render failed; keeping static companion.",error);dispose();}
  };
  document.addEventListener("visibilitychange",onVisibilityChange);
  reduceMotion.addEventListener("change",onMotionChange);
  resizeObserver.observe(canvas);

  void(async()=>{
    try{
      await loadCore();
      const setting=await readJson("model3.json"),refs=setting?.FileReferences;
      const idlePath=refs?.Motions?.Idle?.[0]?.File;
      if(!refs?.Moc||!Array.isArray(refs.Textures)||refs.Textures.length!==1||refs.Textures[0]!=="texture.png"||!idlePath)throw new Error("Unexpected model or idle-motion references");
      const idleMotion=await readJson(idlePath),duration=Number(idleMotion.Meta?.Duration);
      if(!Array.isArray(idleMotion.Curves)||!Number.isFinite(duration)||duration<=0)throw new Error("Invalid idle motion");
      idleMotion.Meta.Duration=duration;
      const mocResponse=await fetch(localAsset("model.moc3"));
      if(!mocResponse.ok)throw new Error("MOC load failed");
      const core=(window as typeof window & {Live2DCubismCore:any}).Live2DCubismCore;
      moc=core.Moc.fromArrayBuffer(await mocResponse.arrayBuffer());model=moc&&core.Model.fromMoc(moc);
      if(!model)throw new Error("Cubism model creation failed");
      baseline=new Float32Array(model.parameters.values);
      maskGroups=buildMaskGroups(model.drawables);
      for(const [key,group] of maskGroups)for(const index of group.drawableIndices)drawableMaskKeys.set(index,key);
      texture=await new Promise<HTMLImageElement>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error("Model texture load failed"));image.src=localAsset("texture.png").href;});
      motion=idleMotion;
      if(disposed){releaseModel();return;}
      canvas.hidden=false;fallback.hidden=true;ready=true;motionStartedAt=performance.now();schedule();
    }catch(error){console.warn("Composer Live2D unavailable; keeping static companion.",error);dispose();}
  })();
  return dispose;
}

