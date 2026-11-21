import {batches,mappings,runs,quarantines} from '../api/business.js';
export const store={batches:[],mappings:[],runs:[],quarantines:[],quarantineVersion:0,user:null};
export async function refresh(){
 const [b,m,r,q]=await Promise.all([batches.list(),mappings.list(),runs.list(),quarantines.list()]);
 store.batches=b;store.mappings=m;store.runs=r;store.quarantines=q.items;store.quarantineVersion=q.version;
}
