/* core.js — 纯计算与格式化，不碰 DOM
 *
 * 这一层只做两件事：把数字变成人看的字符串（money / pct / fmtVal），
 * 和把交易行变成统计量（stats / quantile / bucketize）。
 * 全文没有一处 document 引用，所以能单独测试，也能在别处复用。
 *
 * 想加一个新的统计量、或改某一列的显示格式 → 改这里。
 */
"use strict";

const LABEL={entry_iv:"进场 IV",entry_delta:"进场 delta",entry_moneyness:"进场价值状态",
 entry_dte_calendar:"进场剩余天数",entry_underlying_price:"进场标的价",entry_price:"入场权利金",
 exit_price:"出场权利金",holding_days:"持有交易日",entry_sig_rsi_14:"进场 RSI",
 entry_sig_rv_20:"进场已实现波动率",entry_sig_mom_20:"进场 20 日动量",entry_sig_atr_14:"进场 ATR",
 entry_sig_bb_width_20:"进场布林带宽",ma_gap:"价格相对 20 日均线",trend:"价格相对 200 日均线",
 exit_iv:"出场 IV",exit_delta:"出场 delta"};
const PCT=new Set(["entry_moneyness","ma_gap","trend","entry_sig_mom_20","entry_iv","exit_iv",
 "entry_sig_rv_20","entry_sig_bb_width_20"]);
const REASON={take_profit:"止盈",stop_loss:"止损",time_exit:"持有期满",dte_floor:"剩余到期触底",
 expiry_itm:"被动到期·实值",expiry_otm:"被动到期·虚值",risk_forced:"风控强平"};
const EXCLUDE=new Set(["trade_id","gross_pnl","fees","net_pnl","entry_sig_close",
 "entry_sig_sma_20","entry_sig_sma_50","entry_sig_sma_200"]);

const money=v=>(v>=0?"+":"−")+Math.abs(v).toLocaleString("en-US",{maximumFractionDigits:0});
const pct=v=>(v*100).toFixed(1)+"%";
const sgn=v=>v>0?"pos":v<0?"neg":"";
const label=c=>LABEL[c]||c;
function fmtVal(col,v){
  if(v==null||Number.isNaN(v))return"—";
  if(PCT.has(col))return(v*100).toFixed(1)+"%";
  if(Math.abs(v)>=100)return v.toFixed(0);
  if(Math.abs(v)>=1)return v.toFixed(2);
  return v.toFixed(3);
}
function quantile(s,q){if(!s.length)return NaN;const p=(s.length-1)*q,lo=Math.floor(p),hi=Math.ceil(p);
  return lo===hi?s[lo]:s[lo]+(s[hi]-s[lo])*(p-lo);}
function stats(rows){
  const p=rows.map(r=>r.net_pnl).sort((a,b)=>a-b),n=p.length;
  if(!n)return{n:0,sum:0,mean:0,median:0,win:0,min:0,max:0};
  const sum=p.reduce((a,b)=>a+b,0);
  return{n,sum,mean:sum/n,median:quantile(p,.5),win:p.filter(v=>v>0).length/n,min:p[0],max:p[n-1]};
}
function bucketize(rows,col,k){
  const vals=rows.map(r=>r[col]).filter(v=>v!=null&&!Number.isNaN(v)).sort((a,b)=>a-b);
  if(vals.length<k*2)return null;
  const edges=[vals[0]];
  for(let i=1;i<k;i++){const e=quantile(vals,i/k);if(e>edges[edges.length-1])edges.push(e);}
  edges.push(vals[vals.length-1]+1e-12);
  if(edges.length<3)return null;
  const g=edges.slice(0,-1).map((lo,i)=>({lo,hi:edges[i+1],rows:[]}));
  for(const r of rows){const v=r[col];if(v==null||Number.isNaN(v))continue;
    let i=g.findIndex(x=>v>=x.lo&&v<x.hi);if(i<0)i=g.length-1;g[i].rows.push(r);}
  return g.filter(x=>x.rows.length);
}
