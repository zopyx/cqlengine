#column field types
from copy import copy
from datetime import datetime
import re
import json
from uuid import uuid1, uuid4
from cql.query import cql_quote

from cqlengine.exceptions import ValidationError

class BaseValueManager(object):

    def __init__(self, instance, column, value):
        self.instance = instance
        self.column = column
        self.initial_value = copy(value)
        self.value = value

    @property
    def deleted(self):
        return self.value is None and self.initial_value is not None

    def getval(self):
        return self.value

    def setval(self, val):
        self.value = val

    def delval(self):
        self.value = None

    def get_property(self):
        _get = lambda slf: self.getval()
        _set = lambda slf, val: self.setval(val)
        _del = lambda slf: self.delval()

        if self.column.can_delete:
            return property(_get, _set, _del)
        else:
            return property(_get, _set)

class Column(object):

    #the cassandra type this column maps to
    db_type = None
    value_manager = BaseValueManager

    instance_counter = 0

    def __init__(self, primary_key=False, index=False, db_field=None, default=None, required=True):
        """
        :param primary_key: bool flag, indicates this column is a primary key. The first primary key defined
        on a model is the partition key, all others are cluster keys
        :param index: bool flag, indicates an index should be created for this column
        :param db_field: the fieldname this field will map to in the database
        :param default: the default value, can be a value or a callable (no args)
        :param required: boolean, is the field required?
        """
        self.primary_key = primary_key
        self.index = index
        self.db_field = db_field
        self.default = default
        self.required = required

        #only the model meta class should touch this
        self._partition_key = False

        #the column name in the model definition
        self.column_name = None

        self.value = None

        #keep track of instantiation order
        self.position = Column.instance_counter
        Column.instance_counter += 1

    def validate(self, value):
        """
        Returns a cleaned and validated value. Raises a ValidationError
        if there's a problem
        """
        if value is None:
            if self.has_default:
                return self.get_default()
            elif self.required:
                raise ValidationError('{} - None values are not allowed'.format(self.column_name or self.db_field))
        return value

    def to_python(self, value):
        """
        Converts data from the database into python values
        raises a ValidationError if the value can't be converted
        """
        return value

    def to_database(self, value):
        """
        Converts python value into database value
        """
        if value is None and self.has_default:
            return self.get_default()
        return value

    @property
    def has_default(self):
        return self.default is not None

    @property
    def is_primary_key(self):
        return self.primary_key

    @property
    def can_delete(self):
        return not self.primary_key

    def get_default(self):
        if self.has_default:
            if callable(self.default):
                return self.default()
            else:
                return self.default

    def get_column_def(self):
        """
        Returns a column definition for CQL table definition
        """
        return '"{}" {}'.format(self.db_field_name, self.db_type)

    def set_column_name(self, name):
        """
        Sets the column name during document class construction
        This value will be ignored if db_field is set in __init__
        """
        self.column_name = name

    @property
    def db_field_name(self):
        """ Returns the name of the cql name of this column """
        return self.db_field or self.column_name

    @property
    def db_index_name(self):
        """ Returns the name of the cql index """
        return 'index_{}'.format(self.db_field_name)

class Bytes(Column):
    db_type = 'blob'

class Ascii(Column):
    db_type = 'ascii'

class Text(Column):
    db_type = 'text'

    def __init__(self, *args, **kwargs):
        self.min_length = kwargs.pop('min_length', 1 if kwargs.get('required', True) else None)
        self.max_length = kwargs.pop('max_length', None)
        super(Text, self).__init__(*args, **kwargs)

    def validate(self, value):
        value = super(Text, self).validate(value)
        if value is None: return
        if not isinstance(value, (basestring, bytearray)) and value is not None:
            raise ValidationError('{} is not a string'.format(type(value)))
        if self.max_length:
            if len(value) > self.max_length:
                raise ValidationError('{} is longer than {} characters'.format(self.column_name, self.max_length))
        if self.min_length:
            if len(value) < self.min_length:
                raise ValidationError('{} is shorter than {} characters'.format(self.column_name, self.min_length))
        return value


class JSON(Text):

    def to_python(self, value):
        """
        Converts data from the database into python values
        raises a ValidationError if the value can't be converted
        """
        if value is None:
            return None

        if isinstance(value, basestring):
            try:
                return json.loads(value)
            except (TypeError, ValueError), e:
                raise ValueError(e.message)
        else:
            return value

    def to_database(self, value):
        """
        Converts python value into database value
        """
        if value is None and self.has_default:
            value = self.get_default()
        return json.dumps(value, separators=(',', ':'))

    def validate(self, value):
        try:
            json.dumps(value)
            return value
        except (ValueError, TypeError), e:
            raise ValidationError(e.message)


class Integer(Column):
    db_type = 'int'

    def validate(self, value):
        val = super(Integer, self).validate(value)
        if val is None: return
        try:
            return long(val)
        except (TypeError, ValueError):
            raise ValidationError("{} can't be converted to integral value".format(value))

    def to_python(self, value):
        return self.validate(value)

    def to_database(self, value):
        return self.validate(value)

class DateTime(Column):
    db_type = 'timestamp'
    def __init__(self, **kwargs):
        super(DateTime, self).__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, datetime):
            return value
        return datetime.utcfromtimestamp(value)

    def to_database(self, value):
        value = super(DateTime, self).to_database(value)
        if not isinstance(value, datetime):
            raise ValidationError("'{}' is not a datetime object".format(value))
        epoch = datetime(1970, 1, 1)
        return long((value - epoch).total_seconds() * 1000)

class UUID(Column):
    """
    Type 1 or 4 UUID
    """
    db_type = 'uuid'

    re_uuid = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

    def __init__(self, default=lambda:uuid4(), **kwargs):
        super(UUID, self).__init__(default=default, **kwargs)

    def validate(self, value):
        val = super(UUID, self).validate(value)
        if val is None: return
        from uuid import UUID as _UUID
        if isinstance(val, _UUID): return val
        if not self.re_uuid.match(val):
            raise ValidationError("{} is not a valid uuid".format(value))
        return _UUID(val)

class TimeUUID(UUID):
    """
    UUID containing timestamp
    """

    db_type = 'timeuuid'

    def __init__(self, **kwargs):
        kwargs.setdefault('default', lambda: uuid1())
        super(TimeUUID, self).__init__(**kwargs)

class Boolean(Column):
    db_type = 'boolean'

    def to_python(self, value):
        return bool(value)

    def to_database(self, value):
        return bool(value)

class Float(Column):
    db_type = 'double'

    def __init__(self, double_precision=True, **kwargs):
        self.db_type = 'double' if double_precision else 'float'
        super(Float, self).__init__(**kwargs)

    def validate(self, value):
        value = super(Float, self).validate(value)
        if value is None: return
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValidationError("{} is not a valid float".format(value))

    def to_python(self, value):
        return self.validate(value)

    def to_database(self, value):
        return self.validate(value)

class Decimal(Column):
    db_type = 'decimal'

class Counter(Column):
    #TODO: counter field
    def __init__(self, **kwargs):
        super(Counter, self).__init__(**kwargs)
        raise NotImplementedError

class ContainerValueManager(BaseValueManager):
    pass

class ContainerQuoter(object):
    """
    contains a single value, which will quote itself for CQL insertion statements
    """
    def __init__(self, value):
        self.value = value

    def __str__(self):
        raise NotImplementedError

class BaseContainerColumn(Column):
    """
    Base Container type
    """

    def __init__(self, value_type, **kwargs):
        """
        :param value_type: a column class indicating the types of the value
        """
        if not issubclass(value_type, Column):
            raise ValidationError('value_type must be a column class')
        if issubclass(value_type, BaseContainerColumn):
            raise ValidationError('container types cannot be nested')
        if value_type.db_type is None:
            raise ValidationError('value_type cannot be an abstract column type')

        self.value_type = value_type
        self.value_col = self.value_type()
        super(BaseContainerColumn, self).__init__(**kwargs)

    def get_column_def(self):
        """
        Returns a column definition for CQL table definition
        """
        db_type = self.db_type.format(self.value_type.db_type)
        return '{} {}'.format(self.db_field_name, db_type)

class Set(BaseContainerColumn):
    """
    Stores a set of unordered, unique values

    http://www.datastax.com/docs/1.2/cql_cli/using/collections
    """
    db_type = 'set<{}>'

    class Quoter(ContainerQuoter):

        def __str__(self):
            cq = cql_quote
            return '{' + ', '.join([cq(v) for v in self.value]) + '}'

    def __init__(self, value_type, strict=True, **kwargs):
        """
        :param value_type: a column class indicating the types of the value
        :param strict: sets whether non set values will be coerced to set
            type on validation, or raise a validation error, defaults to True
        """
        self.strict = strict
        super(Set, self).__init__(value_type, **kwargs)

    def validate(self, value):
        val = super(Set, self).validate(value)
        if val is None: return
        types = (set,) if self.strict else (set, list, tuple)
        if not isinstance(val, types):
            if self.strict:
                raise ValidationError('{} is not a set object'.format(val))
            else:
                raise ValidationError('{} cannot be coerced to a set object'.format(val))

        return {self.value_col.validate(v) for v in val}

    def to_database(self, value):
        return self.Quoter({self.value_col.to_database(v) for v in value})

class List(BaseContainerColumn):
    """
    Stores a list of ordered values

    http://www.datastax.com/docs/1.2/cql_cli/using/collections_list
    """
    db_type = 'list<{}>'

    class Quoter(ContainerQuoter):

        def __str__(self):
            cq = cql_quote
            return '[' + ', '.join([cq(v) for v in self.value]) + ']'

    def validate(self, value):
        val = super(List, self).validate(value)
        if val is None: return
        if not isinstance(val, (set, list, tuple)):
            raise ValidationError('{} is not a list object'.format(val))
        return [self.value_col.validate(v) for v in val]

    def to_database(self, value):
        return self.Quoter([self.value_col.to_database(v) for v in value])

class Map(BaseContainerColumn):
    """
    Stores a key -> value map (dictionary)

    http://www.datastax.com/docs/1.2/cql_cli/using/collections_map
    """

    db_type = 'map<{}, {}>'

    class Quoter(ContainerQuoter):

        def __str__(self):
            cq = cql_quote
            return '{' + ', '.join([cq(k) + ':' + cq(v) for k,v in self.value.items()]) + '}'

    def __init__(self, key_type, value_type, **kwargs):
        """
        :param key_type: a column class indicating the types of the key
        :param value_type: a column class indicating the types of the value
        """
        if not issubclass(value_type, Column):
            raise ValidationError('key_type must be a column class')
        if issubclass(value_type, BaseContainerColumn):
            raise ValidationError('container types cannot be nested')
        if key_type.db_type is None:
            raise ValidationError('key_type cannot be an abstract column type')

        self.key_type = key_type
        self.key_col = self.key_type()
        super(Map, self).__init__(value_type, **kwargs)

    def get_column_def(self):
        """
        Returns a column definition for CQL table definition
        """
        db_type = self.db_type.format(
            self.key_type.db_type,
            self.value_type.db_type
        )
        return '{} {}'.format(self.db_field_name, db_type)

    def validate(self, value):
        val = super(Map, self).validate(value)
        if val is None: return
        if not isinstance(val, dict):
            raise ValidationError('{} is not a dict object'.format(val))
        return {self.key_col.validate(k):self.value_col.validate(v) for k,v in val.items()}

    def to_python(self, value):
        if value is not None:
            return {self.key_col.to_python(k):self.value_col.to_python(v) for k,v in value.items()}

    def to_database(self, value):
        return self.Quoter({self.key_col.to_database(k):self.value_col.to_database(v) for k,v in value.items()})


