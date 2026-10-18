import json,sys
def load(p):
    d=json.load(open(p)); d.pop('timings',None); return d
def diffs(a,b):
    out=[]
    def walk(x,y,p=''):
        if type(x)!=type(y): out.append((p,str(x)[:100],str(y)[:100])); return
        if isinstance(x,dict):
            for k in sorted(set(x)|set(y)):
                if k not in x or k not in y: out.append((p+'/'+k,'MISSING' if k not in x else 'present','MISSING' if k not in y else 'present'))
                else: walk(x[k],y[k],p+'/'+k)
        elif isinstance(x,list):
            if len(x)!=len(y): out.append((p,'len %d'%len(x),'len %d'%len(y))); return
            for i,(u,v) in enumerate(zip(x,y)): walk(u,v,p+f'[{i}]')
        elif x!=y: out.append((p,repr(x)[:100],repr(y)[:100]))
    walk(a,b); return out
if __name__=='__main__':
    o=diffs(load(sys.argv[1]),load(sys.argv[2])); print(len(o),'diffs')
    for r in o[:int(sys.argv[3]) if len(sys.argv)>3 else 15]: print(r)
