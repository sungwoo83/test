"""Explicit no-target declarations; no document-verification decisions are inferred."""
import copy
from decimal import Decimal
import management as m


def calculate(plan,values,complete,base):
    if plan['mode']=='text':return base(plan,values,complete)
    if not isinstance(values,dict) or set(values)-{c['id'] for c in plan['components']}:raise m.APIError(400,'세부 실적 항목을 확인해 주세요.')
    adjusted=copy.deepcopy(plan);normal={};excluded=[];exceptions={}
    for c in plan['components']:
        value=values.get(c['id'])
        if isinstance(value,dict) and 'state' in value:
            state=value['state']
            if state=='value':normal[c['id']]=value.get('value');continue
            if state=='missing':normal[c['id']]='';continue
            policy=c.get('no_target_policy','none')
            if state!='no_target' or policy not in ('exclude','full'):raise m.APIError(400,'해당 세부항목은 대상 없음 처리가 허용되지 않습니다.')
            reason=value.get('reason')
            if not isinstance(reason,str) or not 2<=len(reason.strip())<=1000 or m.number(value.get('count')) not in ('0','0.0','0.00','0.000','0.0000','0.00000','0.000000'):
                raise m.APIError(400,'대상 없음은 대상 수 0과 구체적인 사유를 입력해 주세요.')
            exceptions[c['id']]={'state':'excluded' if policy=='exclude' else 'no_target_full','reason':reason.strip(),'count':'0'}
            if policy=='exclude':excluded.append(c['id'])
            else:
                target=next(x for x in adjusted['components'] if x['id']==c['id']);target.update(method='points',floor='0',cap='1');normal[c['id']]=c['weight']
        else:normal[c['id']]=value
    adjusted['components']=[c for c in adjusted['components'] if c['id'] not in excluded]
    weight=sum((Decimal(c['weight']) for c in adjusted['components']),Decimal(0));adjusted['weight']=str(weight)
    result=base(adjusted,normal,complete)
    for part in result['components']:
        if part['id'] in exceptions:part.update(exceptions[part['id']])
        else:part['state']='missing' if part.get('score') is None else 'value'
    for c in plan['components']:
        if c['id'] in excluded:result['components'].append({'id':c['id'],'name':c['name'],'score':'0.000','weight':c['weight'],'target':c['target'],'actual':None,**exceptions[c['id']]})
    result.update(effective_weight=format(weight,'f'),excluded_weight=format(Decimal(plan['weight'])-weight,'f'),fully_excluded=not adjusted['components'])
    return result


def value_of(value):
    if isinstance(value,dict) and value.get('state')=='value':return value.get('value')
    if isinstance(value,dict) and value.get('state')=='missing':return None
    return value


def aggregate_values(plan,selected,dec):
    import json
    result={}
    for c in plan['components']:
        people=[];declarations=[]
        for owner in sorted({r['employee'] for r in selected}):
            raw=[value_of(json.loads(r['values_json']).get(c['id'])) for r in selected if r['employee']==owner]
            if any(v in (None,'') for v in raw):raise m.APIError(400,'확정 실적 세부항목 누락')
            declarations.extend(v for v in raw if isinstance(v,dict) and v.get('state')=='no_target')
            actual=[v for v in raw if not (isinstance(v,dict) and v.get('state')=='no_target')]
            if not actual:continue
            if c['method']=='rate':people.append({'numerator':sum(dec(v['numerator']) for v in actual),'denominator':sum(dec(v['denominator']) for v in actual)})
            else:
                number=sum(dec(v) for v in actual)
                people.append(number/len(actual) if plan['time_aggregation']=='average' else number)
        if not people:result[c['id']]=declarations[-1] if declarations else None
        elif c['method']=='rate':result[c['id']]={'numerator':sum(p['numerator'] for p in people),'denominator':sum(p['denominator'] for p in people)}
        else:result[c['id']]=sum(people)/len(people) if plan['aggregation']=='average' else sum(people)
    return result


def serializable(value):
    if isinstance(value,Decimal):return format(value,'f')
    if isinstance(value,dict):return {k:serializable(v) for k,v in value.items()}
    if isinstance(value,list):return [serializable(v) for v in value]
    return value
