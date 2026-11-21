/**
 * @typedef {{id:number,name:string,source_file:string,rows:object[]}} Batch
 * @typedef {{id:number,name:string,batch_id:number,rules:object[],chunk_size:number}} Mapping
 * @typedef {{id:number,name:string,status:'queued'|'running'|'completed'|'failed'|'cancelled',result:object|null}} Run
 * @typedef {{id:number,code:string,title:string,reason:string,test_name:string,measured_from:string,measured_to:string,status:'draft'|'active'|'revoked',affected:object[],activated_at:string|null,revoked_at:string|null}} Quarantine
 */
export {};
