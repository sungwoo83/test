"""Trusted server-console account setup; passwords never enter source or argv."""
import argparse
import getpass
import app
import management


def read_password():
    password=getpass.getpass('수퍼관리자 비밀번호: ')
    confirmation=getpass.getpass('비밀번호 확인: ')
    if password != confirmation:
        raise ValueError('비밀번호 확인이 일치하지 않습니다.')
    if not 6 <= len(password) <= 128:
        raise ValueError('수퍼관리자 비밀번호는 6~128자로 입력해 주세요.')
    return password


def validate_target(db,args,parser):
    user=db.execute('SELECT * FROM users WHERE employee_id=?',(args.employee_id,)).fetchone()
    if args.command=='add-superadmin':
        if user: parser.error('이미 가입된 사번입니다. 웹의 권한 관리에서 지정해 주세요.')
        if not management.can_be_superadmin(args.department_id,app.CATALOG):
            parser.error('성과혁신실 또는 AI정보융합실을 지정해 주세요.')
        return None
    if args.command in ('bootstrap-superadmin','create-superadmin'):
        if db.execute("SELECT 1 FROM users WHERE role='superadmin'").fetchone():
            parser.error('수퍼관리자가 이미 있습니다. 웹의 권한 관리에서 추가 지정해 주세요.')
        if args.command=='create-superadmin':
            if user: parser.error('이미 가입된 사번입니다. bootstrap-superadmin 명령을 사용해 주세요.')
            if not management.can_be_superadmin(args.department_id,app.CATALOG):
                parser.error('성과혁신실 또는 AI정보융합실을 지정해 주세요.')
            return None
        if not user or not management.can_be_superadmin(user['department_id'],app.CATALOG):
            parser.error('성과혁신실 또는 AI정보융합실 소속으로 가입한 계정을 지정해 주세요.')
    elif not user or user['role']!='superadmin':
        parser.error('이미 수퍼관리자로 지정된 계정만 비밀번호를 설정할 수 있습니다.')
    return user


def main():
    parser=argparse.ArgumentParser(description='서버 운영자용 수퍼관리자 지정·비밀번호 설정')
    parser.add_argument('command',choices=['bootstrap-superadmin','create-superadmin','add-superadmin','set-superadmin-password'])
    parser.add_argument('--employee-id',required=True)
    parser.add_argument('--name',default='',help='계정에 표시할 직원 성명')
    parser.add_argument('--department-id',default='SYS-PERFORMANCE',help='신규 수퍼관리자 소속 부서 ID')
    parser.add_argument('--set-password',action='store_true',help='최초 지정 시 비밀번호도 새로 입력')
    parser.add_argument('--db',type=app.Path,default=app.DB)
    args=parser.parse_args()
    app.DB=args.db
    app.initialize()
    with app.connect() as db:
        validate_target(db,args,parser)
    password=None
    if args.set_password or args.command in ('set-superadmin-password','create-superadmin','add-superadmin'):
        try:
            password=read_password()
        except (ValueError,EOFError) as exc:
            parser.error(str(exc) if isinstance(exc,ValueError) else '비밀번호 입력을 완료해 주세요.')
    encoded=app.password_hash(password) if password is not None else None
    with app.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        user=validate_target(db,args,parser)
        if args.command in ('create-superadmin','add-superadmin'):
            import json
            import re
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',args.employee_id):
                parser.error('사번 형식을 확인해 주세요.')
            if len(args.name)>100: parser.error('성명은 100자 이내로 입력해 주세요.')
            indicators=[k['id'] for k in app.available(args.department_id,db)]
            db.execute("INSERT INTO users(employee_id,password_hash,department_id,indicators,revision,created_at,role,display_name) VALUES(?,?,?,?,1,?,'superadmin',?)",
                (args.employee_id,encoded,args.department_id,json.dumps(indicators),app.now(),args.name.strip()))
            db.execute('INSERT INTO changes(employee_id,after_json,changed_at) VALUES(?,?,?)',
                (args.employee_id,json.dumps({'department_id':args.department_id,'indicators':indicators}),app.now()))
        if args.command=='bootstrap-superadmin':
            db.execute("UPDATE users SET role='superadmin',admin_department_id=NULL WHERE employee_id=?",(args.employee_id,))
        if encoded is not None:
            db.execute('UPDATE users SET password_hash=? WHERE employee_id=?',(encoded,args.employee_id))
            db.execute('DELETE FROM attempts WHERE scope=?',('employee:'+args.employee_id,))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(args.employee_id,))
        management.audit(db,'SERVER-CONSOLE','최초 수퍼관리자 지정' if args.command in ('bootstrap-superadmin','create-superadmin') else ('수퍼관리자 추가' if args.command=='add-superadmin' else '수퍼관리자 비밀번호 설정'),None,args.employee_id,
            {'role':user['role']} if user else None,{'role':'superadmin','password_changed':encoded is not None},'서버 운영자 콘솔에서 설정')
    print('수퍼관리자 설정 완료. 해당 계정으로 다시 로그인하세요.')


if __name__=='__main__':main()
