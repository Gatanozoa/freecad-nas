import * as THREE from 'three';
import {STLLoader} from './vendor/STLLoader.js';
import {OrbitControls} from './vendor/OrbitControls.js';

export async function mountViewer(host,url){
  const response=await fetch(url);if(!response.ok)throw Error('预览文件读取失败，仍可查看任务或重新生成');
  const geometry=new STLLoader().parse(await response.arrayBuffer());geometry.computeBoundingBox();geometry.center();geometry.computeVertexNormals();
  const size=geometry.boundingBox.getSize(new THREE.Vector3()), span=Math.max(size.x,size.y,size.z),scene=new THREE.Scene();scene.background=new THREE.Color('#242529');
  const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:'#c9c3ba',roughness:.65,metalness:.05}));scene.add(mesh);
  scene.add(new THREE.HemisphereLight(0xffffff,0x70665a,2.5));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(2,4,3);scene.add(light);
  const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setSize(host.clientWidth,host.clientHeight);host.replaceChildren(renderer.domElement);
  const camera=new THREE.PerspectiveCamera(38,host.clientWidth/host.clientHeight,span/1000,span*100);camera.up.set(0,0,1);camera.position.set(span*1.5,-span*1.8,span*1.4);
  const controls=new OrbitControls(camera,renderer.domElement);controls.update();controls.addEventListener('change',render);function render(){renderer.render(scene,camera)}
  const resize=new ResizeObserver(()=>{if(!host.clientWidth||!host.clientHeight)return;camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight);render()});resize.observe(host);render();
  const label=document.createElement('span');label.className='viewer-label';label.textContent='单位 mm · 单指旋转 / 双指缩放';host.append(label);
  const toolbar=document.createElement('div');toolbar.className='viewer-tools';host.append(toolbar);
  const directions={'立体':[1.5,-1.8,1.4],'正视':[0,-1,0],'俯视':[0,0,1],'右视':[1,0,0]};
  function view(name){const d=directions[name];camera.up.set(0,0,1);camera.position.set(...d.map(x=>x*span*2));controls.target.set(0,0,0);controls.update();render()}
  for(const name of Object.keys(directions)){const b=document.createElement('button');b.textContent=name;b.onclick=()=>view(name);toolbar.append(b)}
  function drawings(){const out=[];for(const name of ['正视','俯视','右视']){const half=span*.65, ortho=new THREE.OrthographicCamera(-half,half,half,-half,span/1000,span*100);ortho.up.set(0,name==='俯视'?1:0,name==='俯视'?0:1);ortho.position.set(...directions[name].map(x=>x*span*3));ortho.lookAt(0,0,0);const oldSize=renderer.getSize(new THREE.Vector2());renderer.setSize(700,700,false);renderer.render(scene,ortho);out.push({name,url:renderer.domElement.toDataURL('image/png')});renderer.setSize(oldSize.x,oldSize.y,false)}render();return out}
  return {drawings,snapshot(){render();return renderer.domElement.toDataURL('image/png')},dispose(){resize.disconnect();controls.dispose();geometry.dispose();mesh.material.dispose();renderer.dispose();renderer.forceContextLoss();host.replaceChildren()}};
}
